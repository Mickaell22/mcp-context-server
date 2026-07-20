import os

import db
from config import DEVICE_ID


async def handle(args: dict, session_id: int | None) -> dict:
    projects = db.get_all_projects()
    result = []
    for p in projects:
        path = db.resolve_project_path(p, DEVICE_ID)
        registered = bool((p.get("device_paths") or {}).get(DEVICE_ID))
        result.append(
            {
                "name": p["name"],
                # Ruta para ESTE dispositivo (para comparar con el cwd actual).
                "path": path,
                # True si el proyecto tiene ruta local registrada en este equipo
                # y esa ruta existe en disco. Si es False, la ruta pertenece a
                # otro dispositivo: registralo aca con register_project.
                "on_this_device": registered and os.path.isdir(path),
                "device_id": DEVICE_ID,
                "last_indexed": p["last_indexed_at"].isoformat() if p["last_indexed_at"] else None,
                "repo_url": p["repo_url"],
            }
        )
    return {"projects": result}
