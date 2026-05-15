from __future__ import annotations

import logging
import db
import security
import retriever
import deepseek_client
from db import log_query

logger = logging.getLogger(__name__)

BACKEND_QUERIES: list[tuple[str, str]] = [
    ("security",        "autenticacion, autorizacion, permisos, roles, control de acceso, JWT, tokens"),
    ("multitenancy",    "aislamiento entre tenants, organizacion, empresa, cliente, datos compartidos"),
    ("rate_limiting",   "rate limiting, throttling, limites de uso, cuotas, burst"),
    ("input_validation","validacion de entrada, sanitizacion, inyeccion SQL, XSS, CSRF"),
    ("error_handling",  "manejo de errores, excepciones no capturadas, logging, monitoring, alertas"),
    ("deprecated",      "codigo obsoleto, TODO, FIXME, HACK, deprecated, legacy, workaround"),
    ("tests",           "tests unitarios, cobertura, fixtures, mocks, integracion, casos de prueba"),
    ("api_contracts",   "endpoints REST, contratos de API, serializacion, versionado, paginacion"),
]

FRONTEND_QUERIES: list[tuple[str, str]] = [
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
]

# Estrategia de recuperación por categoría.
# semantic_disabled: omite búsqueda semántica, solo usa recuperación estructural.
# structural_patterns: patrones ILIKE para recuperar todos los chunks de los archivos coincidentes.
# import_patterns: igual pero solo chunk 0 de cada archivo (donde viven los imports).
# prompt_hint: texto prepuesto al prompt de auditoría para guiar al modelo.
CATEGORY_STRATEGY: dict[str, dict] = {
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
                chunks = retriever.retrieve(query, project["id"], code_only=True)

            structural_pats = strategy.get("structural_patterns", [])
            if structural_pats:
                chunks = _dedup(chunks + _structural_chunks(project["id"], structural_pats))

            import_pats = strategy.get("import_patterns", [])
            if import_pats:
                chunks = _dedup(chunks + _structural_chunks(project["id"], import_pats, first_chunk_only=True))

            if not chunks:
                report[category] = {"findings": "Sin patrones relevantes encontrados.", "files_referenced": []}
                continue

            hint = strategy.get("prompt_hint", "")
            prefix = f"INSTRUCCIONES ESPECIALES: {hint}\n\n" if hint else ""
            audit_query = f"{prefix}Auditoria de {category} — busca problemas, ausencias o patrones relacionados con: {query}"
            context, in_tok, out_tok, cost = deepseek_client.compress_context(audit_query, chunks)

            files = list(dict.fromkeys(c["file_path"] for c in chunks))
            report[category] = {
                "findings": context,
                "files_referenced": files,
                "tokens": in_tok + out_tok,
            }
            total_input += in_tok
            total_output += out_tok
            total_cost += cost

            if session_id is not None:
                log_query(
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

    return {
        "project": project_name,
        "project_type": project_type,
        "categories_checked": len(queries_to_run),
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "audit": report,
    }
