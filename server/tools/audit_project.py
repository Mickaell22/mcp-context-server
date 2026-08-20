from __future__ import annotations

import asyncio
import logging
import re
import db
import security
import retriever
import deepseek_client
from config import (
    AUDIT_TOP_K,
    AUDIT_MAX_CHUNKS,
    AUDIT_RAW_MAX_CHARS,
    AUDIT_VERIFY_ENABLED,
    AUDIT_BATCH_MAX_CHARS,
)

logger = logging.getLogger(__name__)

# Parseo de hallazgos estructurados para la pasada de consolidación.
_SEV_RE = re.compile(r"\*\*\[(CR[IÍ]TICO|ALTO|MEDIO|BAJO)\]\*\*\s*(.+)")
_SEV_NORM = {"CRITICO": "CRÍTICO", "CRÍTICO": "CRÍTICO", "ALTO": "ALTO", "MEDIO": "MEDIO", "BAJO": "BAJO"}
_SEV_ORDER = {"CRÍTICO": 0, "ALTO": 1, "MEDIO": 2, "BAJO": 3}

# Dedupe entre categorías: el mismo problema suele salir en 2+ categorías con
# otra redacción (ej. un except silenciado en security Y en error_handling).
_LOCATION_RE = re.compile(r"`([^`\s]+:[\d\w–-]+)`")
_WORD_RE = re.compile(r"[\wá-úÁ-Ú./:]+")
_DEDUPE_JACCARD = 0.55


def _finding_location(detail: str) -> str | None:
    m = _LOCATION_RE.search(detail)
    return m.group(1).lower() if m else None


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

    # Se ordena ANTES de dedupear para que sobreviva la copia de mayor severidad.
    found.sort(key=lambda f: _SEV_ORDER.get(f["severity"], 9))

    # Duplicado = misma cita `archivo:línea`, o redacción muy parecida (Jaccard de
    # palabras) aunque venga de otra categoría.
    # ponytail: scan O(n²) sobre decenas de hallazgos; si algún día n>500, indexar por archivo.
    uniq: list[dict] = []
    kept_locs: list[str | None] = []
    kept_words: list[set[str]] = []
    for f in found:
        loc = _finding_location(f["detail"])
        words = set(_WORD_RE.findall(f["detail"].lower()))
        dup = False
        for kloc, kwords in zip(kept_locs, kept_words):
            if loc and loc == kloc:
                dup = True
                break
            union = len(words | kwords)
            if union and len(words & kwords) / union >= _DEDUPE_JACCARD:
                dup = True
                break
        if not dup:
            uniq.append(f)
            kept_locs.append(loc)
            kept_words.append(words)

    counts: dict[str, int] = {}
    for f in uniq:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"total": len(uniq), "by_severity": counts, "top": uniq[:25]}

# ---------- Verificación de hallazgos (segunda pasada anti-falsos-positivos) ----------
# El auditor ve EXTRACTOS y a veces reporta como CRÍTICO cosas que el resto del
# archivo desmiente (import "faltante" que existe, patrón idiomático marcado como
# race). Esta pasada re-lee el archivo completo citado por cada CRÍTICO/ALTO y
# pide veredicto. Una sola llamada DeepSeek extra por audit; fail-open (si la
# llamada falla o el veredicto no parsea, el hallazgo se conserva).

_VERIFY_MAX_FINDINGS = 12

_VERIFY_INSTRUCTIONS = (
    "Eres un revisor ESCÉPTICO de hallazgos de auditoría. Abajo van hallazgos numerados "
    "y el código COMPLETO de los archivos que citan. Contrasta cada hallazgo contra el "
    "código literal:\n"
    "- CONFIRMADO: el código muestra exactamente el problema descrito.\n"
    "- DESCARTADO: el código lo desmiente (el import/validación/manejo sí existe, el "
    "patrón es correcto o idiomático del framework, o el hallazgo depende de un supuesto "
    "que el código no respalda).\n"
    "- REBAJADO A MEDIO o REBAJADO A BAJO: el problema existe pero la severidad está "
    "inflada (no es pérdida de datos, brecha de seguridad ni crash demostrable).\n"
    "Responde SOLO una línea por hallazgo, sin texto adicional:\n"
    "N: CONFIRMADO|DESCARTADO|REBAJADO A MEDIO|REBAJADO A BAJO — motivo breve"
)

_VERDICT_RE = re.compile(
    r"^\s*(\d+)\s*[:.\)]\s*(CONFIRMADO|DESCARTADO|REBAJADO\s+A\s+(?:MEDIO|BAJO))"
    r"\s*(?:[—–-]\s*(.*))?$",
    re.I | re.M,
)


def _verify_summary(project_id: int, summary: dict) -> tuple[int, int, float]:
    """Verifica los hallazgos CRÍTICO/ALTO del summary contra el archivo completo
    que citan. Muta summary: quita los DESCARTADOS (quedan en summary['descartados']),
    rebaja severidades infladas y marca el veredicto en cada hallazgo. Retorna
    (input_tokens, output_tokens, costo)."""
    candidates = [f for f in summary["top"] if f["severity"] in ("CRÍTICO", "ALTO")]
    candidates = candidates[:_VERIFY_MAX_FINDINGS]

    # Resolver el archivo citado de cada hallazgo a paths indexados.
    verifiable: list[dict] = []
    files_needed: list[str] = []
    for f in candidates:
        loc = _finding_location(f["detail"])
        if not loc:
            continue
        cited_file = loc.split(":", 1)[0]
        matches = db.get_files_by_path_patterns(project_id, [f"%{cited_file}"])
        if not matches:
            continue
        verifiable.append(f)
        for m in matches[:2]:
            if m not in files_needed:
                files_needed.append(m)
    if not verifiable:
        return 0, 0, 0.0

    # Código completo de los archivos citados, capado al presupuesto de la ventana.
    context_chunks: list[dict] = []
    used = 0
    for fp in files_needed:
        file_chunks = retriever.get_file_chunks(project_id, fp)
        size = sum(len(c["content"]) for c in file_chunks)
        if context_chunks and used + size > AUDIT_BATCH_MAX_CHARS:
            break
        for c in file_chunks:
            context_chunks.append({**c, "file_path": fp})
        used += size
    if not context_chunks:
        return 0, 0, 0.0

    listado = "\n".join(
        f"{i + 1}. [{f['severity']}] ({f['category']}) {f['detail']}"
        for i, f in enumerate(verifiable)
    )
    prompt = (
        f"{_VERIFY_INSTRUCTIONS}\n\n"
        f"HALLAZGOS A VERIFICAR:\n{listado}\n\n"
        f"CÓDIGO COMPLETO DE LOS ARCHIVOS CITADOS:\n\n"
        f"{deepseek_client._build_fragments(context_chunks)}"
    )
    text, in_tok, out_tok, cost = deepseek_client._call(prompt, context_chunks)

    verdicts: dict[int, tuple[str, str]] = {}
    for m in _VERDICT_RE.finditer(text):
        idx = int(m.group(1)) - 1
        verdicts[idx] = (m.group(2).upper(), (m.group(3) or "").strip())

    # Los conteos se ajustan de forma incremental: top está capado a 25 pero
    # total/by_severity cuentan TODOS los hallazgos, no solo los del top.
    counts = summary["by_severity"]
    descartados: list[dict] = []
    for i, f in enumerate(verifiable):
        verdict, motivo = verdicts.get(i, ("CONFIRMADO", ""))
        if verdict == "DESCARTADO":
            f["verdict"] = "descartado"
            f["motivo_descarte"] = motivo
            descartados.append(f)
            counts[f["severity"]] = counts.get(f["severity"], 1) - 1
            summary["total"] -= 1
        elif verdict.startswith("REBAJADO"):
            f["verdict"] = "rebajado"
            counts[f["severity"]] = counts.get(f["severity"], 1) - 1
            f["severity"] = "BAJO" if verdict.endswith("BAJO") else "MEDIO"
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        else:
            f["verdict"] = "confirmado"

    if descartados:
        summary["top"] = [f for f in summary["top"] if f not in descartados]
        summary["descartados"] = descartados
    summary["by_severity"] = {k: v for k, v in counts.items() if v > 0}
    summary["top"].sort(key=lambda f: _SEV_ORDER.get(f["severity"], 9))
    summary["verificados"] = len(verifiable)
    return in_tok, out_tok, cost


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
    # ponytail: correctness es categoría compartida back/front y la estrategia es
    # única (no se ramifica por project_type); el bloque React va condicionado en
    # el propio texto, así el backend simplemente no lo matchea.
    "Si el código es React/JSX, además: dependencias de useEffect faltantes o de más "
    "(closure que lee estado viejo), índice del array como `key` en listas que se "
    "reordenan o filtran, mutación directa de estado (push/splice/asignación a una "
    "variable de useState) en vez de copia inmutable, setState tras un await en un "
    "componente que pudo desmontarse, input que pasa de no-controlado a controlado "
    "(value de undefined a un valor). "
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

# --- Pistas frontend destiladas de skills externas -------------------------
# Conocimiento de Vercel React Best Practices (70 reglas de performance) y Web
# Interface Guidelines (a11y/forms), horneado como señales en el prompt en vez de
# fetchear reglas en runtime. Filosofía del usuario: Claude Code usa las skills
# para CONSTRUIR; el MCP usa su conocimiento destilado para REVISAR lo ya
# construido, gastando menos tokens con la misma calidad de revisión.
# ponytail: conocimiento externo -> hint. Cero deps, cero tools, cero red.

_PERFORMANCE_HINT = (
    "Detecta problemas de performance React/Next CONCRETOS (no estilo). Reporta: "
    "1) [ALTO] Waterfalls de datos: awaits secuenciales independientes que deberían ir en "
    "Promise.all; fetch en cascada padre->hijo paralelizable; await de un flag remoto antes "
    "de comprobar una condición sync barata. "
    "2) [ALTO] Componente o función-componente definido DENTRO de otro componente: se recrea "
    "en cada render y remonta su subárbol. Debe ir a nivel de módulo. "
    "3) [MEDIO] Estado derivado calculado con useEffect+setState cuando se puede derivar "
    "durante el render (provoca un render extra o un loop). "
    "4) [MEDIO] Trabajo caro en cada render (filter/map/sort de listas grandes, new Date, regex, "
    "new de objetos) sin useMemo; lista larga sin virtualización ni content-visibility. "
    "5) [MEDIO] Dependencias de efecto no primitivas (objeto/array/función inline) que rompen la "
    "memoización; handler pasado a un hijo ENVUELTO en React.memo sin useCallback (solo cuenta si "
    "el hijo está realmente memoizado). "
    "6) [BAJO] Componente pesado sin next/dynamic ni React.lazy; render condicional con `&&` sobre "
    "un número (puede pintar 0/NaN — usa ternario). "
    "GUARDARRAIL (estricto, decisivo): si un componente NO tiene hijos en React.memo, ni listas "
    "grandes, ni cálculos costosos en render, entonces useMemo/useCallback/memo NO aportan nada y "
    "NO debes mencionarlos. NUNCA sugieras envolver en useCallback un handler trivial (un setState "
    "directo, un toggle): memoizar de más es sobre-optimización con coste propio. Ante la duda, no "
    "lo reportes. Indica archivo, línea y patrón."
)

_STATE_MANAGEMENT_HINT = (
    "Reporta problemas de gestión de estado React: "
    "1) [MEDIO] estado que en realidad es DERIVADO de props u otro estado y se guarda con "
    "useState+useEffect (debe calcularse durante el render, no sincronizarse con un efecto). "
    "2) [MEDIO] prop drilling profundo (la misma prop cruzando 3+ niveles) que pide Context o "
    "composición de componentes. "
    "3) [MEDIO] setState basado en el valor previo sin forma funcional (setX(x+1) en vez de "
    "setX(v => v+1)) dentro de callbacks o efectos. "
    "4) [BAJO] estado global (Context/Redux/Zustand) para algo que solo usa un componente; "
    "suscripción a estado que solo se lee dentro de un callback (renders de más). "
    "NO marques estado local legítimo ni Context bien acotado. Indica archivo y línea."
)

# error_handling es categoría compartida back/front: el hint cubre ambos lados y
# cada uno ignora el medio que no le aplica. Antes corría sin hint en ambos sets.
_ERROR_HANDLING_HINT = (
    "Reporta manejo de errores deficiente. "
    "En frontend/React: componentes que hacen fetch y solo cubren el happy path (falta el trío "
    "loading/empty/error); promesas sin .catch ni try/catch que dejan la UI colgada en 'Cargando'; "
    "ausencia de Error Boundary alrededor de subárboles que pueden lanzar; acceso a `data` "
    "potencialmente null/undefined sin guard tras el fetch. "
    "En backend: except que traga la excepción (except: pass, except Exception sin re-raise ni log), "
    "errores logueados pero no propagados, recursos sin cerrar cuando salta una excepción, "
    "respuestas de error que filtran stacktrace o detalles internos al cliente. "
    "Indica archivo y línea."
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
        # Señales de Web Interface Guidelines (Vercel) horneadas: la skill original
        # fetchea las reglas por HTTP; aquí van fijas (son estables y DeepSeek no
        # va a fetchear). La semántica no ve ausencias, por eso se lee el JSX entero.
        "prompt_hint": (
            "La búsqueda semántica no detecta ausencias. Analiza el JSX y reporta: "
            "<img> sin atributo alt (o con alt vacío en imagen informativa), "
            "<button>/<a> sin texto visible ni aria-label, "
            "<div>/<span> con onClick sin role='button', tabIndex ni manejo de teclado (Enter/Espacio), "
            "input sin <label htmlFor> asociado ni aria-label, "
            "campo de formulario sin type/autocomplete/inputmode adecuados (email, tel, numeric), "
            "elemento interactivo que elimina el outline de foco sin alternativa :focus-visible, "
            "jerarquía de headings rota (ej. h2 antes de h1, salto h1→h3), "
            "color como ÚNICO indicador de estado (error solo en rojo, sin texto ni icono), "
            "animación/transición que ignora prefers-reduced-motion, "
            "página sin landmarks semánticos (<main>/<nav>/<header>, todo en <div>). "
            "Indica archivo, línea y el elemento problemático."
        ),
    },
    "performance": {"prompt_hint": _PERFORMANCE_HINT},
    "state_management": {"prompt_hint": _STATE_MANAGEMENT_HINT},
    "error_handling": {"prompt_hint": _ERROR_HANDLING_HINT},
    "imports": {
        # Los chunks son extractos: el modelo reportaba "falta el import X" cuando
        # el import existía en la cabecera (fuera del fragmento). Falso positivo real.
        "prompt_hint": (
            "Solo reporta problemas de imports DEMOSTRABLES dentro del fragmento: import "
            "duplicado visible, dependencia circular evidente, import relativo frágil. "
            "NUNCA reportes 'falta el import X' ni 'import no utilizado': el fragmento "
            "casi nunca incluye la cabecera completa del archivo ni todos sus usos."
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


# La recuperación estructural vive en retriever (la comparte describe_project).
_structural_chunks = retriever.chunks_by_path_patterns


def _raw_fragments_within(chunks: list[dict], budget: float, category: str) -> tuple[str, int]:
    """Fragmentos crudos de una categoría acotados al presupuesto de caracteres
    restante. Corta en límite de chunk (nunca a mitad de código) y, si omite
    chunks, lo dice y sugiere pedir la categoría sola. Retorna (texto, chars)."""
    parts: list[str] = []
    used = 0
    for c in chunks:
        frag = deepseek_client._build_fragments([c])
        if parts and used + len(frag) > budget:
            break
        parts.append(frag)
        used += len(frag)
    omitted = len(chunks) - len(parts)
    text = "\n\n---\n\n".join(parts)
    if omitted:
        text += (
            f"\n\n[{omitted} de {len(chunks)} chunks omitidos por presupuesto "
            f"(AUDIT_RAW_MAX_CHARS). Para verlos completos pide esta categoría sola: "
            f"categories=['{category}'].]"
        )
    return text, used


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


def _contract_chunks(project_a: dict, project_b: dict) -> tuple[list[dict], list[str], str, str]:
    """Retrieval del contrato API entre dos proyectos pareados (frontend + backend).
    Solo recupera: la llamada al modelo la hace la pool de la fase B junto con las
    categorías. Retorna (chunks, files, nombre_frontend, nombre_backend)."""
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
    files = list(dict.fromkeys(c["file_path"] for c in chunks))
    return chunks, files, frontend["name"], backend["name"]


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
    # El total va acotado por AUDIT_RAW_MAX_CHARS: sin tope, un repo mediano devuelve
    # 300K+ chars que saturan el contexto del que llama.
    raw = bool(args.get("raw", False))
    raw_budget_left: float = AUDIT_RAW_MAX_CHARS if AUDIT_RAW_MAX_CHARS > 0 else float("inf")

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

    # ---------- Fase A: retrieval (secuencial) ----------
    # Chroma + Postgres + embeddings se quedan en un solo hilo: son rápidos y así
    # no hay que razonar sobre su thread-safety. Lo que se paraleliza (fase B) es
    # únicamente el I/O de red contra DeepSeek, que es lo que tardaba minutos.
    jobs: list[tuple[str, str, list[dict]]] = []
    job_files: dict[str, list[str]] = {}
    job_queries: dict[str, str] = {}

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
                        text, used = _raw_fragments_within(chunks, raw_budget_left, category)
                        raw_budget_left -= used
                        report[category] = {"findings": text, "files_referenced": files, "tokens": 0, "raw": True}
                        continue
                    fallback_instr = f"Categoría '{category}': revisa el código disponible y busca problemas relacionados con: {query}"
                    jobs.append((category, fallback_instr, chunks))
                    job_files[category] = files
                    job_queries[category] = query
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
                if raw_budget_left <= 0:
                    report[category] = {
                        "findings": (
                            f"[Presupuesto raw agotado (AUDIT_RAW_MAX_CHARS). Para ver esta "
                            f"categoría pídela sola: categories=['{category}'].]"
                        ),
                        "files_referenced": files,
                        "tokens": 0,
                        "raw": True,
                    }
                    continue
                text, used = _raw_fragments_within(chunks, raw_budget_left, category)
                raw_budget_left -= used
                report[category] = {"findings": text, "files_referenced": files, "tokens": 0, "raw": True}
                continue

            hint = strategy.get("prompt_hint", "")
            instructions = f"Categoría '{category}'. Busca problemas, ausencias o patrones relacionados con: {query}."
            if hint:
                instructions += f"\nGuía específica: {hint}"

            jobs.append((category, instructions, chunks))
            job_files[category] = files
            job_queries[category] = query

        except Exception as exc:
            logger.error("Fallo en categoria %s: %s", category, exc, exc_info=True)
            report[category] = {"findings": f"Error durante la auditoría: {exc}", "files_referenced": []}

    # Contrato cross-repo: su retrieval también va en fase A para que sus lotes
    # entren en la MISMA pool que las categorías, en vez de correr después.
    contract_meta: dict | None = None
    if paired_project is not None:
        try:
            chunks, files, front_name, back_name = _contract_chunks(project, paired_project)
            if not chunks:
                report["contracts"] = {"findings": "Sin código de API/endpoints para comparar.", "files_referenced": [], "tokens": 0}
            elif raw:
                text, _ = _raw_fragments_within(chunks, raw_budget_left, "contracts")
                report["contracts"] = {"findings": text, "files_referenced": files, "tokens": 0, "raw": True}
            else:
                jobs.append(("contracts", _CONTRACT_INSTRUCTIONS, chunks))
                job_files["contracts"] = files
                contract_meta = {"front": front_name, "back": back_name}
        except Exception as exc:
            logger.error("Fallo en auditoría de contratos: %s", exc, exc_info=True)
            report["contracts"] = {"findings": f"Error durante la auditoría de contratos: {exc}", "files_referenced": []}

    # ---------- Fase B: llamadas a DeepSeek (paralelas) ----------
    # to_thread para no bloquear el event loop del server MCP mientras corre la pool.
    results: dict[str, tuple[str, int, int, float]] = {}
    if jobs:
        try:
            results = await asyncio.to_thread(deepseek_client.audit_batches, jobs)
        except Exception as exc:
            logger.error("Fallo en la pasada de auditoría: %s", exc, exc_info=True)
            for key, _, _ in jobs:
                report[key] = {"findings": f"Error durante la auditoría: {exc}", "files_referenced": job_files.get(key, [])}

    # ---------- Fase C: ensamblado (secuencial) ----------
    for key, _, _ in jobs:
        if key not in results:
            continue
        context, in_tok, out_tok, cost = results[key]
        report[key] = {
            "findings": context,
            "files_referenced": job_files.get(key, []),
            "tokens": in_tok + out_tok,
        }
        total_input += in_tok
        total_output += out_tok
        total_cost += cost

        if session_id is not None:
            if key == "contracts" and contract_meta:
                query_text = f"[audit:contracts] {contract_meta['front']} <-> {contract_meta['back']}"
            else:
                query_text = f"[audit:{key}] {job_queries.get(key, '')}"
            db.log_query(
                session_id=session_id,
                query_text=query_text,
                response_text=context,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=cost,
            )

    # Consolidación: ranking por severidad entre categorías (no aplica en modo raw)
    summary = None
    if not raw:
        summary = _consolidate(report)
        if AUDIT_VERIFY_ENABLED:
            try:
                v_in, v_out, v_cost = _verify_summary(project["id"], summary)
                total_input += v_in
                total_output += v_out
                total_cost += v_cost
            except Exception as exc:
                # fail-open: la verificación nunca tumba el audit ni borra hallazgos
                logger.error("Fallo en verificación de hallazgos: %s", exc, exc_info=True)

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
    if summary is not None:
        result["summary"] = summary

    # Un audit degradado (DeepSeek no respondio) devuelve chunks crudos con 0
    # tokens y summary vacio: indistinguible de "todo limpio". Se marca explicito.
    degradadas = sorted(
        k for k, v in report.items()
        if isinstance(v.get("findings"), str) and deepseek_client.RAW_FALLBACK_MARKER in v["findings"]
    )
    if degradadas:
        result["llm_available"] = False
        result["warning"] = (
            f"DeepSeek no respondio en: {', '.join(degradadas)}. Esas categorias traen "
            "los fragmentos crudos, NO hallazgos: la ausencia de findings no significa "
            "que el codigo este limpio. Revisa el log del server y reintenta."
        )
    return result
