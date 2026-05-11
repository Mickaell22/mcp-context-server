import db
import security
import retriever
import deepseek_client
from db import log_blocked_attempt, log_query


async def handle(args: dict, session_id: int | None) -> dict:
    query = args.get("query", "").strip()
    project_name = args.get("project", "").strip()

    if not query or not project_name:
        return {"error": "Se requieren 'query' y 'project'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado"}

    if not security.is_path_allowed(project["path"]):
        log_blocked_attempt(session_id, project["path"], "proyecto fuera de whitelist")
        return {"error": f"Proyecto '{project_name}' no esta en la whitelist"}

    chunks = retriever.retrieve(query, project["id"])
    if not chunks:
        return {"context": "", "files_referenced": [], "tokens_used": 0}

    context, input_tokens, output_tokens, cost = deepseek_client.compress_context(query, chunks)

    files_referenced = list(dict.fromkeys(c["file_path"] for c in chunks))

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
