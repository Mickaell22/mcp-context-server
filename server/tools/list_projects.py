import os

import db
import git_client
import retriever
import self_update
from config import DEVICE_ID
from tools.check_updates import is_index_stale


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
        here = registered and os.path.isdir(path)
        last_indexed = (p.get("device_indexed_at") or {}).get(DEVICE_ID)
        # Aviso de indice viejo SIN red (solo lee el repo en disco): asi llega en
        # la llamada que ya se hace al entrar a un directorio, sin tener que
        # acordarse de pedir check_updates. El estado contra GitHub (behind/ahead)
        # necesita fetch y vive en check_updates.
        stale = is_index_stale(git_client.local_state(path), last_indexed) if here else None
        result.append(
            {
                "name": p["name"],
                # Ruta para ESTE dispositivo (para comparar con el cwd actual).
                "path": path,
                # True si el proyecto tiene ruta local registrada en este equipo
                # y esa ruta existe en disco. Si es False, la ruta pertenece a
                # otro dispositivo: registralo aca con register_project.
                "on_this_device": here,
                "device_id": DEVICE_ID,
                # Cuando lo indexo ESTE equipo (None si nunca, o si lo indexo
                # antes de que existiera device_indexed_at).
                "last_indexed": last_indexed,
                # True si hay commits posteriores al ultimo indexado de este
                # equipo (None si el proyecto no esta en este disco).
                "index_stale": stale,
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
    out = {"projects": result}

    # Aviso pasivo de version nueva del PROPIO server. Va aca porque list_projects
    # es la llamada que ya se hace al entrar a cualquier proyecto: asi el aviso
    # llega solo. El fetch esta cacheado (CHECK_TTL_SECONDS), no es uno por llamada.
    try:
        estado = self_update.check()
        if estado.get("update_available"):
            out["server_update"] = {
                "available": True,
                "behind": estado.get("behind"),
                "commits": estado.get("commits", [])[:5],
                "action": "check_server_version(update=true) y despues reiniciar Claude Code",
            }
    except Exception:
        pass  # que falle el chequeo de version jamas debe romper list_projects

    return out
