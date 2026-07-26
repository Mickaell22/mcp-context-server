import os

import db
import retriever
from config import DEVICE_ID


async def handle(args: dict, session_id: int | None) -> dict:
    projects = db.get_all_projects()
    # Que puede consultar ESTE equipo se mide contra su Chroma, no contra un
    # registro en la Postgres compartida: un proyecto indexado por otra maquina
    # figura al dia y aca no tiene un solo vector.
    sin_chunks = set(retriever.projects_without_chunks([p["id"] for p in projects]))
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
                # Cuando lo indexo ESTE equipo (None si nunca, o si lo indexo
                # antes de que existiera device_indexed_at).
                "last_indexed": (p.get("device_indexed_at") or {}).get(DEVICE_ID),
                # Si es False, este equipo NO puede consultarlo: hay que correr
                # index_project aca aunque figure indexado en la lista.
                "indexed_here": p["id"] not in sin_chunks,
                # Ultimo indexado por CUALQUIER equipo (solo informativo).
                "last_indexed_anywhere": (
                    p["last_indexed_at"].isoformat() if p["last_indexed_at"] else None
                ),
                "repo_url": p["repo_url"],
            }
        )
    return {"projects": result}
