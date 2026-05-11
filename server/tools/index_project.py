import db
import security
import indexer


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = args.get("project", "").strip()

    if not project_name:
        return {"error": "Se requiere 'project'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado en la base de datos"}

    valid, reason = security.validate_project_path(project["path"])
    if not valid:
        return {"error": reason}

    files_indexed = indexer.index_project(project["id"], project["path"])

    return {
        "project": project_name,
        "files_indexed": files_indexed,
    }
