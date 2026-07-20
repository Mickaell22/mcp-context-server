from __future__ import annotations

import os
import db
import security
import indexer
from config import DEVICE_ID


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

    # Politica de registro: permitimos rutas fuera de PROJECTS_BASE_PATH, pero
    # toda ruta registrada queda en la whitelist (en memoria) para que luego sea
    # consultable e indexable. No usamos validate_project_path aqui a proposito:
    # no queremos rechazar rutas fuera de la base, solo autorizarlas explicitamente.
    #
    # Multi-dispositivo: si el proyecto ya existe (registrado desde otro equipo),
    # NO pisamos su registro; solo agregamos la ruta local de ESTE dispositivo.
    existing = db.get_project_by_name(name)
    if existing:
        project_id = existing["id"]
        already = bool((existing.get("device_paths") or {}).get(DEVICE_ID))
    else:
        project_id = db.insert_project(name, path)
        already = False

    db.set_device_path(project_id, DEVICE_ID, path)
    security.add_allowed_path(path)

    files_indexed, file_list = indexer.index_project(project_id, path)

    return {
        "project": name,
        "path": path,
        "device_id": DEVICE_ID,
        "device_path_updated": not already,
        "files_indexed": files_indexed,
        "files": file_list,
    }
