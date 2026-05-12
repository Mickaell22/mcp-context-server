from __future__ import annotations

import os
import db
import security
import indexer


async def handle(args: dict, session_id: int | None) -> dict:
    path = args.get("path", "").strip()
    name = args.get("name", "").strip()

    if not path:
        return {"error": "Se requiere 'path'"}

    path = os.path.realpath(path)

    if not os.path.isdir(path):
        return {"error": f"El directorio no existe: {path}"}

    if not name:
        name = os.path.basename(path)

    valid, reason = security.validate_project_path(path)
    if not valid:
        # si esta fuera de PROJECTS_BASE_PATH lo permitimos igual pero lo registramos
        security.add_allowed_path(path)

    project_id = db.insert_project(name, path)
    security.add_allowed_path(path)

    files_indexed, file_list = indexer.index_project(project_id, path)

    return {
        "project": name,
        "path": path,
        "files_indexed": files_indexed,
        "files": file_list,
    }
