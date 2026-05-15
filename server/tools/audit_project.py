from __future__ import annotations

import db
import security
import retriever
import deepseek_client
from db import log_query

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
    ("seo",                "meta tags, title, description, Open Graph, og:image, canonical, structured data, semantic HTML, h1, h2"),
    ("component_design",   "component size, props interface, TypeScript types, PropTypes, reusability, single responsibility, composition"),
    ("error_handling",     "error boundary, ErrorBoundary, try catch, loading state, empty state, skeleton, fallback UI, null check"),
    ("deprecated",         "TODO, FIXME, HACK, @ts-ignore, @ts-expect-error, any type, eslint-disable, deprecated, legacy"),
    ("tests",              "React Testing Library, render, fireEvent, userEvent, Playwright, Cypress, snapshot, screen.getBy"),
    ("bundle_size",        "import pesado, lodash, moment, date-fns, bundle, tree shaking, side effects, package size, barrel exports, index re-export"),
    ("hydration",          "useEffect, useLayoutEffect, typeof window, isMounted, suppressHydrationWarning, SSR mismatch, client only, next/dynamic, ssr false, localStorage en render"),
]

_BACKEND_MAP = dict(BACKEND_QUERIES)
_FRONTEND_MAP = dict(FRONTEND_QUERIES)
ALL_CATEGORIES = set(_BACKEND_MAP) | set(_FRONTEND_MAP)


def _detect_project_type(project_id: int) -> str:
    ext_counts = db.get_file_extensions(project_id)
    frontend = sum(ext_counts.get(e, 0) for e in {".tsx", ".jsx"})
    backend = sum(ext_counts.get(e, 0) for e in {".py", ".java", ".go", ".rs", ".cs"})
    return "frontend" if frontend > backend else "backend"


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
        chunks = retriever.retrieve(query, project["id"], code_only=True)

        if not chunks:
            report[category] = {"findings": "Sin patrones relevantes encontrados.", "files_referenced": []}
            continue

        audit_query = f"Auditoria de {category} — busca problemas, ausencias o patrones relacionados con: {query}"
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

    return {
        "project": project_name,
        "project_type": project_type,
        "categories_checked": len(queries_to_run),
        "total_tokens": total_input + total_output,
        "total_cost_usd": round(total_cost, 6),
        "audit": report,
    }
