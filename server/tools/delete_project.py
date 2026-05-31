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

    # 1. Borrar los chunks (embeddings) de ChromaDB
    try:
        indexer.delete_project_chunks(project["id"])
    except Exception as e:
        return {"error": f"No se pudieron borrar los chunks de ChromaDB: {e}"}

    # 2. Borrar filas en Postgres (queries/sessions/indexed_files/file_imports/proyecto)
    db.delete_project(project["id"])

    # 3. Sacar la ruta de la whitelist en memoria
    security.remove_allowed_path(project["path"])

    return {
        "project": project_name,
        "path": project["path"],
        "deleted": True,
    }
