from __future__ import annotations

import db
import security


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = args.get("project", "").strip()
    symbol = args.get("symbol", "").strip()

    if not project_name or not symbol:
        return {"error": "Se requieren 'project' y 'symbol'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado"}

    if not security.is_path_allowed(project["path"]):
        return {"error": f"Proyecto '{project_name}' no esta en la whitelist"}

    files = db.find_files_importing(project["id"], symbol)

    return {
        "project": project_name,
        "symbol": symbol,
        "used_in": files,
        "count": len(files),
    }
