from __future__ import annotations

import db
import security
import retriever
import deepseek_client
from db import log_blocked_attempt, log_query


async def handle(args: dict, session_id: int | None) -> dict:
    query = args.get("query", "").strip()
    project_arg = args.get("project", "")
    code_only = args.get("code_only", False)

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
    chunks = retriever.retrieve(query, project_ids, code_only=code_only)

    if not chunks:
        return {"context": "", "files_referenced": [], "tokens_used": 0}

    context, input_tokens, output_tokens, cost = deepseek_client.compress_context(query, chunks)

    files_referenced = list(dict.fromkeys(
        f"{c['project_id']}:{c['file_path']}" if len(projects) > 1 else c["file_path"]
        for c in chunks
    ))

    if session_id is not None:
        log_query(
            session_id=session_id,
            query_text=query,
            response_text=context,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    return {
        "context": context,
        "files_referenced": files_referenced,
        "tokens_used": input_tokens + output_tokens,
    }
