import os

import db
import security
import indexer


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = args.get("project", "").strip()
    incremental = args.get("incremental", False)

    if not project_name:
        return {"error": "Se requiere 'project'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado en la base de datos"}

    # Validamos contra la whitelist (no contra PROJECTS_BASE_PATH): un proyecto
    # registrado fuera de la base sigue siendo re-indexable. Lo unico que exigimos
    # es que su ruta este permitida y siga existiendo en disco.
    if not security.is_path_allowed(project["path"]):
        return {"error": f"Proyecto '{project_name}' no esta en la whitelist: {project['path']}"}

    if not os.path.isdir(project["path"]):
        return {"error": f"La ruta del proyecto ya no existe en disco: {project['path']}"}

    files_indexed, file_list = indexer.index_project(project["id"], project["path"], incremental=incremental)

    return {
        "project": project_name,
        "files_indexed": files_indexed,
        "total_files": len(file_list),
        "mode": "incremental" if incremental else "full",
    }
