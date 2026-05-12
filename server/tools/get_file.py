from __future__ import annotations

import db
import security
import retriever


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = args.get("project", "").strip()
    file_path = args.get("file_path", "").strip()

    if not project_name or not file_path:
        return {"error": "Se requieren 'project' y 'file_path'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado"}

    if not security.is_path_allowed(project["path"]):
        return {"error": f"Proyecto '{project_name}' no esta en la whitelist"}

    chunks = retriever.get_file_chunks(project["id"], file_path)
    if not chunks:
        return {"error": f"Archivo '{file_path}' no encontrado en el indice de '{project_name}'"}

    content = "".join(c["content"] for c in chunks)

    return {
        "project": project_name,
        "file_path": file_path,
        "content": content,
        "chunks": len(chunks),
    }
