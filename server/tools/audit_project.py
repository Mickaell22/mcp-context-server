from __future__ import annotations

import logging
import re
import db
import security
import retriever
import deepseek_client
from config import AUDIT_TOP_K, AUDIT_MAX_CHUNKS

logger = logging.getLogger(__name__)

# Parseo de hallazgos estructurados para la pasada de consolidación.
_SEV_RE = re.compile(r"\*\*\[(CR[IÍ]TICO|ALTO|MEDIO|BAJO)\]\*\*\s*(.+)")
_SEV_NORM = {"CRITICO": "CRÍTICO", "CRÍTICO": "CRÍTICO", "ALTO": "ALTO", "MEDIO": "MEDIO", "BAJO": "BAJO"}
_SEV_ORDER = {"CRÍTICO": 0, "ALTO": 1, "MEDIO": 2, "BAJO": 3}


def _consolidate(report: dict) -> dict:
    """Recolecta los hallazgos con severidad de todas las categorías, dedupe y
    los ordena de mayor a menor severidad. Determinista, sin coste de LLM extra:
    evita que los hallazgos críticos queden sepultados bajo ruido de estilo."""
    found: list[dict] = []
    for category, data in report.items():
        text = (data or {}).get("findings", "") or ""
        for line in text.splitlines():
            m = _SEV_RE.search(line)
            if not m:
                continue
            sev = _SEV_NORM.get(m.group(1).upper(), m.group(1).upper())
            found.append({"severity": sev, "category": category, "detail": m.group(2).strip()})

    seen: set[str] = set()
    uniq: list[dict] = []
    for f in found:
        key = f["detail"][:80].lower()
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    uniq.sort(key=lambda f: _SEV_ORDER.get(f["severity"], 9))

    counts: dict[str, int] = {}
    for f in uniq:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"total": len(uniq), "by_severity": counts, "top": uniq[:25]}

# Pista compartida back/front: la búsqueda semántica engancha mal los bugs de
# lógica (matchea comentarios antes que un `if x is not None`), así que la guía
# explícita de anti-patrones es lo que da recall en esta categoría.
_CORRECTNESS_HINT = (
    "Caza BUGS de lógica reales, no estilo. Reporta: "
    "1) updates parciales que ignoran null/empty (ej. `if data.campo is not None: model.campo = data.campo` "
    "impide limpiar el campo cuando el cliente manda null). "
    "2) etiquetas/conteos que no coinciden con el valor calculado (ej. mostrar 'subtotal (N items)' "
    "usando el total de items cuando el monto solo suma los que cumplen una condición). "
    "3) condiciones invertidas o con operador equivocado (</<=, and/or, == vs is). "
    "4) off-by-one, slicing o paginación incorrectos. "
    "5) early returns o except que silencian datos/errores. "
    "6) comparaciones de igualdad con float, o mezcla Decimal/float. "
    "7) variables mutables compartidas / closures obsoletos. "
    "Indica archivo, función y el snippet exacto."
)

# Pista de la categoría `over-engineering`: complejidad innecesaria, no bugs.
# La búsqueda semántica engancha mejor las abstracciones (tienen nombres:
# interfaz, factory, wrapper) que los bugs de lógica, pero igual necesita una
# guía explícita de qué reportar y, sobre todo, qué NO tocar (guardarrail).
_OVER_ENGINEERING_HINT = (
    "Detecta COMPLEJIDAD INNECESARIA (no bugs): código que funciona pero bajó más "
    "peldaños de los necesarios en la escalera (1.YAGNI 2.stdlib 3.feature nativa "
    "4.dependencia ya instalada 5.una línea). Reporta: "
    "1) [MEDIO] Abstracción prematura: interfaces, clases abstractas, factories o "
    "wrappers con UNA sola implementación o UN solo uso; indirección que solo delega. "
    "2) [ALTO] Dependencia evitable: librería externa para algo que la stdlib o una "
    "feature nativa ya hace (left-pad, cliente HTTP entero para un GET, moment para "
    "formatear una fecha, lodash para map/filter). Nombra la alternativa nativa exacta. "
    "3) [MEDIO] Reinvención de la rueda: parser CSV/JSON a mano, pool de threads casero, "
    "cache LRU manual cuando el runtime ya lo da battle-tested. Indica el reemplazo. "
    "4) [BAJO] Boilerplate: getters/setters triviales, DTOs espejo, mappers 1:1. "
    "En el Fix prioriza BORRAR sobre reescribir e indica el peldaño (1-6) que aplica. "
    "GUARDARRAIL (crítico, ante la duda NO reportes): NUNCA marques como sobre-ingeniería "
    "la validación de input en límites de confianza (red, archivos, API pública, "
    "formularios), el manejo de errores que evita pérdida de datos, la seguridad (authz, "
    "sanitización, hashing, secretos), la accesibilidad, la concurrencia necesaria (locks, "
    "transacciones, idempotencia), los reintentos por red/hardware ni los tests legítimos. "
    "Si es un BUG (comportamiento incorrecto) va en `correctness`, no aquí: aquí solo lo "
    "que FUNCIONA pero se puede borrar sin perder correctness, seguridad ni robustez."
)

# Query semántica común a backend y frontend (misma cadena en ambos maps para que
# {**_BACKEND_MAP, **_FRONTEND_MAP} no genere una key con dos valores distintos).
_OVER_ENGINEERING_QUERY = (
    "interfaz o clase abstracta con una sola implementacion, factory o wrapper que solo delega, "
    "dependencia externa para una tarea trivial que ya hace la stdlib, codigo que reimplementa a mano "
    "un parser cache o pool, parametro o flag sin ningun caller, getter setter trivial o DTO espejo, "
    "indireccion excesiva, clase con estado donde bastaba una funcion pura"
)

BACKEND_QUERIES: list[tuple[str, str]] = [
    ("correctness",     "bug de logica, actualizacion parcial que ignora null, condicion invertida, off-by-one, valor calculado que no coincide con su etiqueta, comparacion is/==, manejo de None vs vacio, return temprano que silencia datos"),
    ("security",        "autenticacion, autorizacion, permisos, roles, control de acceso, JWT, tokens, API keys, secrets hardcodeados, variables de entorno"),
    ("error_handling",  "manejo de errores, excepciones no capturadas, logging, monitoring, alertas, try/except, fallos silenciosos"),
    ("code_quality",    "codigo repetido, funciones muy largas, complejidad ciclomatica, principios SOLID, modularidad, acoplamiento"),
    ("deprecated",      "codigo obsoleto, TODO, FIXME, HACK, deprecated, legacy, workaround, print debugging"),
    ("config_secrets",   "credenciales hardcodeadas, API keys en codigo, .env, configuracion insegura, informacion sensible"),
    ("imports",         "imports no utilizados, dependencias circulares, imports relativos, organizacion de imports"),
    ("io_operations",   "archivos abiertos sin cerrar, timeouts de red, manejo de APIs externas, descargas, operaciones bloqueantes"),
    ("tests",           "tests unitarios, cobertura, fixtures, mocks, integracion, casos de prueba, assertions"),
    ("over-engineering", _OVER_ENGINEERING_QUERY),
]

FRONTEND_QUERIES: list[tuple[str, str]] = [
    ("correctness",        "bug de logica, valor calculado que no coincide con su etiqueta o conteo, condicion invertida, estado obsoleto, dependencias de useEffect incorrectas, off-by-one, comparacion equivocada, manejo de null/undefined"),
    ("accessibility",      "ARIA labels, aria-label, aria-hidden, role, tabIndex, alt text, keyboard navigation, focus, screen reader"),
    ("performance",        "useMemo, useCallback, React.memo, lazy, Suspense, dynamic import, re-renders, code splitting, virtualization"),
    ("state_management",   "useState, useReducer, useContext, Context, Redux, Zustand, prop drilling, global state, side effects"),
    ("seo",                "export const metadata, generateMetadata, title, description, Open Graph, og:image, canonical, structured data"),
    ("component_design",   "component size, props interface, TypeScript types, PropTypes, reusability, single responsibility, composition"),
    ("error_handling",     "error boundary, ErrorBoundary, try catch, loading state, empty state, skeleton, fallback UI, null check"),
    ("deprecated",         "TODO, FIXME, HACK, @ts-ignore, @ts-expect-error, any type, eslint-disable, deprecated, legacy"),
    ("tests",              "React Testing Library, render, fireEvent, userEvent, Playwright, Cypress, snapshot, screen.getBy"),
    ("bundle_size",        "import pesado, lodash, moment, date-fns, bundle, tree shaking, side effects, package size, barrel exports, index re-export"),
    ("hydration",          "useEffect, useLayoutEffect, typeof window, isMounted, suppressHydrationWarning, SSR mismatch, client only, next/dynamic, ssr false, localStorage en render"),
    ("theming",            "variables CSS, :root, .dark, tokens de diseño, contraste, color, prose, tailwind bg text border, muted foreground background, hardcoded color, hex rgb hsl"),
    ("over-engineering",   _OVER_ENGINEERING_QUERY),
]

# Estrategia de recuperación por categoría.
# semantic_disabled: omite búsqueda semántica, solo usa recuperación estructural.
# structural_patterns: patrones ILIKE para recuperar todos los chunks de los archivos coincidentes.
# import_patterns: igual pero solo chunk 0 de cada archivo (donde viven los imports).
# prompt_hint: texto prepuesto al prompt de auditoría para guiar al modelo.
CATEGORY_STRATEGY: dict[str, dict] = {
    "over-engineering": {
        # La señal "dependencia evitable" (la de mayor severidad) es invisible para la
        # búsqueda semántica de abstracciones: hay que mirar el manifiesto de deps.
        # El resto de señales (abstracción, reinvención, boilerplate) las trae la semántica.
        "structural_patterns": ["%requirements.txt", "%package.json"],
        "prompt_hint": _OVER_ENGINEERING_HINT,
    },
    "correctness": {
        # Semántica sola tiene recall bajo para bugs de lógica; sumamos barrido
        # estructural de las carpetas donde vive la lógica de negocio para que las
        # funciones/handlers lleguen completos al modelo.
        "structural_patterns": [
            "%/routers/%", "%/routes/%", "%/controllers/%", "%/api/%",
            "%/services/%", "%/pages/%", "%/views/%", "%/hooks/%",
        ],
        "prompt_hint": _CORRECTNESS_HINT,
    },
    "seo": {
        # El objeto `metadata` y `generateMetadata` viven en page.tsx/layout.tsx.
        # La búsqueda semántica a veces trae solo imports; la recuperación estructural
        # garantiza el contenido completo de esos archivos.
        "structural_patterns": ["%page.tsx", "%layout.tsx", "%page.js", "%layout.js"],
    },
    "bundle_size": {
        # package.json completo como contexto fijo + primer chunk de cada componente
        # (donde están los imports) en lugar de depender de la búsqueda semántica.
        "structural_patterns": ["%package.json"],
        "import_patterns": ["%/components/%", "%/app/%", "%/pages/%"],
        "prompt_hint": (
            "Analiza: 1) dependencias en package.json — identifica paquetes pesados "
            "(lodash, moment, date-fns, antd completo, etc.), "
            "2) imports de librería completa en vez de selectivos "
            "(ej. import _ from 'lodash' en vez de import get from 'lodash/get'), "
            "3) barrel exports en index.ts que re-exportan directorios enteros e impiden tree-shaking."
        ),
    },
    "accessibility": {
        # La búsqueda semántica solo encuentra presencias (aria-label presente).
        # Para detectar ausencias hay que leer el JSX completo de cada componente.
        "semantic_disabled": True,
        "structural_patterns": ["%/components/%", "%/app/%", "%/pages/%"],
        "prompt_hint": (
            "La búsqueda semántica no detecta ausencias. Analiza el JSX y reporta: "
            "<img> sin atributo alt (o con alt vacío), "
            "<button>/<a> sin texto visible ni aria-label, "
            "<div>/<span> con onClick sin role='button' ni aria-*, "
            "inputs sin <label> asociado ni aria-label, "
            "jerarquía de headings rota (ej. h2 antes de h1, salto h1→h3). "
            "Indica archivo y elemento problemático."
        ),
    },
    "theming": {
        # globals.css/tailwind.config completos para ver todos los tokens definidos.
        # import_patterns en %.css/%.scss trae chunk 0 de cada CSS (donde viven :root vars).
        # import_patterns en %/app/%/%/pages/% trae chunk 0 de páginas (donde se usan los tokens).
        # La búsqueda semántica complementa encontrando uso concreto de clases en componentes.
        "structural_patterns": [
            "%globals.css", "%globals.scss", "%globals.sass",
            "%tailwind.config%",
        ],
        "import_patterns": [
            "%.css", "%.scss", "%.sass",
            "%/app/%", "%/pages/%",
        ],
        "prompt_hint": (
            "Analiza theming y contraste. Busca: "
            "1) Variables CSS en :root sin contraparte en .dark {} "
            "(token sin valor en modo oscuro — el componente hereda el valor light). "
            "2) Overrides de prose-* incompletos: si prose-pre:bg-X está definido "
            "sin prose-pre:text-Y ni prose-pre:dark:text-Y, el texto puede contrastar "
            "mal con el fondo personalizado. "
            "3) Colores hardcodeados (#hex, rgb(), hsl()) fuera de variables CSS "
            "que no se adaptan al modo oscuro. "
            "4) Combinaciones Tailwind de bg-*/text-* de luminosidad similar en el mismo elemento "
            "(bajo contraste). "
            "Indica archivo y problema concreto."
        ),
    },
}

_BACKEND_MAP = dict(BACKEND_QUERIES)
_FRONTEND_MAP = dict(FRONTEND_QUERIES)
ALL_CATEGORIES = set(_BACKEND_MAP) | set(_FRONTEND_MAP)


def _detect_project_type(project_id: int) -> str:
    ext_counts = db.get_file_extensions(project_id)
    frontend = sum(ext_counts.get(e, 0) for e in {".tsx", ".jsx"})
    backend = sum(ext_counts.get(e, 0) for e in {".py", ".java", ".go", ".rs", ".cs"})
    return "frontend" if frontend > backend else "backend"


def _structural_chunks(project_id: int, patterns: list[str], first_chunk_only: bool = False) -> list[dict]:
    """Recupera chunks de archivos indexados que coincidan con los patrones ILIKE dados."""
    file_paths = db.get_files_by_path_patterns(project_id, patterns)
    chunks: list[dict] = []
    for fp in file_paths:
        file_chunks = retriever.get_file_chunks(project_id, fp)
        if first_chunk_only:
            file_chunks = file_chunks[:1]
        for chunk in file_chunks:
            chunks.append({
                "file_path": fp,
                "project_id": project_id,
                "chunk_index": chunk["chunk_index"],
                "start_line": chunk.get("start_line"),
                "symbols": chunk.get("symbols", ""),
                "content": chunk["content"],
                "distance": 0.0,
            })
    return chunks


def _dedup(chunks: list[dict]) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    result: list[dict] = []
    for c in chunks:
        key = (c["file_path"], c["chunk_index"])
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


# Patrones de archivos relevantes para el contrato API según el rol del proyecto.
_FRONTEND_API_PATTERNS = ["%/api/%", "%api.js", "%api.ts", "%/services/%", "%/lib/api%"]
_FRONTEND_API_QUERY = "llamadas axios o fetch a endpoints del backend, metodos HTTP, payload o body enviado, parametros, headers"
_BACKEND_ROUTE_PATTERNS = ["%/routers/%", "%/routes/%", "%/controllers/%", "%/api/%", "%/endpoints/%", "%/views/%"]
_BACKEND_ROUTE_QUERY = "endpoints REST, request body, response model, campos opcionales, validacion de entrada, manejo de null, update parcial"

_CONTRACT_INSTRUCTIONS = (
    "Estás comparando un FRONTEND y un BACKEND del mismo sistema. Verifica el CONTRATO "
    "entre el cliente API del frontend y los endpoints del backend. Cada fragmento lleva "
    "su repo entre corchetes en el encabezado. Reporta SOLO desajustes concretos:\n"
    "1) Campos que el frontend envía como null o '' para LIMPIAR un valor pero el backend "
    "ignora con guards tipo `if x is not None` / `if x:` (el campo nunca se borra).\n"
    "2) Nombres de campo que no coinciden entre el request del front y lo que lee el back.\n"
    "3) Tipos incompatibles (string vs number, formato de fecha, array vs objeto).\n"
    "4) Endpoints o métodos HTTP que el front llama y el back no define (o al revés).\n"
    "5) Campos requeridos por el back que el front no envía.\n"
    "Para cada hallazgo indica el archivo del front Y el del back implicados."
)

# Tope de chunks para acotar costo del contrato (front + back combinados).
_CONTRACT_MAX_CHUNKS = 40


def _gather_contract_chunks(project: dict, patterns: list[str], query: str, role: str) -> list[dict]:
    """Recupera chunks relevantes al contrato de un proyecto (semántico + estructural)
    y prefija el repo en file_path para que el modelo distinga front de back."""
    pid = project["id"]
    semantic = retriever.retrieve(query, pid, top_k=AUDIT_TOP_K, code_only=True)
    structural = _structural_chunks(pid, patterns)
    combined = _dedup(semantic + structural)
    tag = f"[{role}:{project['name']}]"
    for c in combined:
        c["file_path"] = f"{tag} {c['file_path']}"
    return combined


async def _contract_audit(project_a: dict, project_b: dict, session_id: int | None, raw: bool = False) -> dict:
    """Audita el contrato API entre dos proyectos pareados (frontend + backend)."""
    type_a = _detect_project_type(project_a["id"])
    type_b = _detect_project_type(project_b["id"])

    # decidir cuál es frontend y cuál backend; si empatan, A=front por convención
    if type_b == "frontend" and type_a != "frontend":
        frontend, backend = project_b, project_a
    else:
        frontend, backend = project_a, project_b

    front_chunks = _gather_contract_chunks(frontend, _FRONTEND_API_PATTERNS, _FRONTEND_API_QUERY, "FRONTEND")
    back_chunks = _gather_contract_chunks(backend, _BACKEND_ROUTE_PATTERNS, _BACKEND_ROUTE_QUERY, "BACKEND")

    chunks = (front_chunks + back_chunks)[:_CONTRACT_MAX_CHUNKS]
    if not chunks:
        return {"findings": "Sin código de API/endpoints para comparar.", "files_referenced": [], "tokens": 0}

    files = list(dict.fromkeys(c["file_path"] for c in chunks))
    if raw:
        return {"findings": deepseek_client._build_fragments(chunks), "files_referenced": files, "tokens": 0, "raw": True}

    context, in_tok, out_tok, cost = deepseek_client.audit_context(_CONTRACT_INSTRUCTIONS, chunks)

    if session_id is not None:
        db.log_query(
            session_id=session_id,
            query_text=f"[audit:contracts] {frontend['name']} <-> {backend['name']}",
            response_text=context,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
        )

    return {
        "findings": context,
        "files_referenced": files,
        "tokens": in_tok + out_tok,
        "_cost": cost,
        "_in": in_tok,
        "_out": out_tok,
    }


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = args.get("project", "").strip()
    requested = args.get("categories", None)

    if not project_name:
        return {"error": "Se requiere 'project'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado"}

    if not security.is_path_allowed(project["path"]):
        return {"error": f"Proyecto '{project_name}' no esta en la whitelist"}

    # raw: devuelve los chunks crudos numerados sin compresión DeepSeek (alta fidelidad
    # para que el modelo que llama razone directamente sobre el código; coste 0 de LLM).
    raw = bool(args.get("raw", False))

    # paired_with: proyecto hermano (front/back) para auditar el contrato API entre ambos
    paired_name = (args.get("paired_with") or "").strip()
    paired_project = None
    if paired_name:
        paired_project = db.get_project_by_name(paired_name)
        if not paired_project:
            return {"error": f"Proyecto pareado '{paired_name}' no encontrado"}
        if not security.is_path_allowed(paired_project["path"]):
            return {"error": f"Proyecto pareado '{paired_name}' no esta en la whitelist"}

    project_type: str
    if requested is not None:
        invalid = set(requested) - ALL_CATEGORIES
        if invalid:
            return {"error": f"Categorias invalidas: {sorted(invalid)}. Validas: {sorted(ALL_CATEGORIES)}"}
        combined = {**_BACKEND_MAP, **_FRONTEND_MAP}
        queries_to_run = [(k, combined[k]) for k in requested if k in combined]
        project_type = "custom"
    else:
        project_type = _detect_project_type(project["id"])
        queries_to_run = FRONTEND_QUERIES if project_type == "frontend" else BACKEND_QUERIES

    report: dict = {}
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for category, query in queries_to_run:
        try:
            strategy = CATEGORY_STRATEGY.get(category, {})

            chunks: list[dict] = []

            if not strategy.get("semantic_disabled"):
                chunks = retriever.retrieve(query, project["id"], top_k=AUDIT_TOP_K, code_only=True)

            structural_pats = strategy.get("structural_patterns", [])
            if structural_pats:
                chunks = _dedup(chunks + _structural_chunks(project["id"], structural_pats))

            import_pats = strategy.get("import_patterns", [])
            if import_pats:
                chunks = _dedup(chunks + _structural_chunks(project["id"], import_pats, first_chunk_only=True))

            if not chunks:
                # Fallback: get general overview of all files
                all_chunks = retriever.retrieve("codigo, funciones, clases, estructura general del proyecto", project["id"])
                if all_chunks:
                    chunks = all_chunks[:5]  # Use top 5 most relevant
                    files = list(dict.fromkeys(c["file_path"] for c in chunks))
                    if raw:
                        report[category] = {"findings": deepseek_client._build_fragments(chunks), "files_referenced": files, "tokens": 0, "raw": True}
                        continue
                    fallback_instr = f"Categoría '{category}': revisa el código disponible y busca problemas relacionados con: {query}"
                    context, in_tok, out_tok, cost = deepseek_client.audit_context(fallback_instr, chunks)
                    report[category] = {"findings": context, "files_referenced": files, "tokens": in_tok + out_tok}
                    total_input += in_tok
                    total_output += out_tok
                    total_cost += cost
                    continue
                report[category] = {"findings": "Sin patrones relevantes encontrados.", "files_referenced": []}
                continue

            # Tope opcional de chunks por categoría (AUDIT_MAX_CHUNKS=0 → sin tope).
            # En producción acota costo; en pruebas se deja en 0 para recall máximo.
            if AUDIT_MAX_CHUNKS and len(chunks) > AUDIT_MAX_CHUNKS:
                logger.info("Categoria %s: %d chunks recortados a %d (AUDIT_MAX_CHUNKS)", category, len(chunks), AUDIT_MAX_CHUNKS)
                chunks = chunks[:AUDIT_MAX_CHUNKS]

            files = list(dict.fromkeys(c["file_path"] for c in chunks))

            if raw:
                report[category] = {"findings": deepseek_client._build_fragments(chunks), "files_referenced": files, "tokens": 0, "raw": True}
                continue

            hint = strategy.get("prompt_hint", "")
            instructions = f"Categoría '{category}'. Busca problemas, ausencias o patrones relacionados con: {query}."
            if hint:
                instructions += f"\nGuía específica: {hint}"
            context, in_tok, out_tok, cost = deepseek_client.audit_context(instructions, chunks)

            report[category] = {
                "findings": context,
                "files_referenced": files,
                "tokens": in_tok + out_tok,
            }
            total_input += in_tok
            total_output += out_tok
            total_cost += cost

            if session_id is not None:
                db.log_query(
                    session_id=session_id,
                    query_text=f"[audit:{category}] {query}",
                    response_text=context,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost,
                )

        except Exception as exc:
            logger.error("Fallo en categoria %s: %s", category, exc, exc_info=True)
            report[category] = {"findings": f"Error durante la auditoría: {exc}", "files_referenced": []}

    # Auditoría de contrato cross-repo (solo si se pasó paired_with)
    if paired_project is not None:
        try:
            contract = await _contract_audit(project, paired_project, session_id, raw=raw)
            total_input += contract.pop("_in", 0)
            total_output += contract.pop("_out", 0)
            total_cost += contract.pop("_cost", 0.0)
            report["contracts"] = contract
        except Exception as exc:
            logger.error("Fallo en auditoría de contratos: %s", exc, exc_info=True)
            report["contracts"] = {"findings": f"Error durante la auditoría de contratos: {exc}", "files_referenced": []}

    result = {
        "project": project_name,
        "project_type": project_type,
        "paired_with": paired_name or None,
        "mode": "raw" if raw else "compressed",
        "categories_checked": len(queries_to_run) + (1 if paired_project is not None else 0),
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "audit": report,
    }
    # Consolidación: ranking por severidad entre categorías (no aplica en modo raw)
    if not raw:
        result["summary"] = _consolidate(report)
    return result
