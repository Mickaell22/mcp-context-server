from __future__ import annotations

import db
import security
import retriever
import deepseek_client
from config import TOP_K_RESULTS, DEVICE_ID
from db import log_blocked_attempt, log_query


async def handle(args: dict, session_id: int | None) -> dict:
    query = args.get("query", "").strip()
    project_arg = args.get("project", "")
    code_only = args.get("code_only", False)

    # top_k opcional: para proyectos grandes 8 chunks puede quedarse corto
    top_k = args.get("top_k") or TOP_K_RESULTS
    try:
        top_k = max(1, int(top_k))
    except (TypeError, ValueError):
        top_k = TOP_K_RESULTS

    if not query or not project_arg:
        return {"error": "Se requieren 'query' y 'project'"}

    # soporta string unico o lista de proyectos
    project_names = [project_arg] if isinstance(project_arg, str) else project_arg

    projects = []
    for name in project_names:
        p = db.get_project_by_name(name.strip())
        if not p:
            return {"error": f"Proyecto '{name}' no encontrado"}
        if not security.is_path_allowed(p["path"]):
            log_blocked_attempt(session_id, p["path"], "proyecto fuera de whitelist")
            return {"error": f"Proyecto '{name}' no esta en la whitelist"}
        projects.append(p)

    project_ids = [p["id"] for p in projects]
    chunks = retriever.retrieve(query, project_ids, top_k=top_k, code_only=code_only)

    if not chunks:
        # Distinguir "no hay match" de "este equipo no tiene el indice": el
        # segundo caso se veia igual (context vacio) y hacia parecer que el
        # codigo no existia, cuando faltaba indexar localmente.
        sin_indice = retriever.projects_without_chunks(project_ids)
        empty = {"context": "", "files_referenced": [], "locations": [], "tokens_used": 0}
        if sin_indice:
            faltan = [p["name"] for p in projects if p["id"] in sin_indice]
            empty["warning"] = (
                f"Sin indice local en este dispositivo ({DEVICE_ID}) para: {', '.join(faltan)}. "
                f"El indice vectorial no se comparte entre equipos: corre "
                f"index_project(project='{faltan[0]}') aca para poder consultarlo."
            )
        return empty

    context, input_tokens, output_tokens, cost = deepseek_client.compress_context(query, chunks)

    files_referenced = list(dict.fromkeys(
        f"{c['project_id']}:{c['file_path']}" if len(projects) > 1 else c["file_path"]
        for c in chunks
    ))

    # Ubicaciones crudas (archivo + rango de lineas + simbolos) para poder ir
    # directo a editar sin re-grepear. El 'context' comprimido pierde esta traza.
    multi = len(projects) > 1
    locations = [
        {
            "file": c["file_path"],
            "start_line": c.get("start_line"),
            "end_line": c.get("end_line"),
            "symbols": c.get("symbols") or None,
            **({"project_id": c["project_id"]} if multi else {}),
        }
        for c in chunks
    ]

    if session_id is not None:
        log_query(
            session_id=session_id,
            query_text=query,
            response_text=context,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    out = {
        "context": context,
        "files_referenced": files_referenced,
        "locations": locations,
        "tokens_used": input_tokens + output_tokens,
    }
    if deepseek_client.RAW_FALLBACK_MARKER in context:
        # Sin esto, "contexto crudo sin comprimir" pasa por una respuesta normal.
        out["llm_available"] = False
        out["warning"] = "DeepSeek no respondio: 'context' son los fragmentos crudos, sin sintetizar."
    return out
