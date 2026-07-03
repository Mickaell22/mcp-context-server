import os

import db
import security
import indexer
import git_client


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = args.get("project", "").strip()
    incremental = args.get("incremental", False)
    acknowledge_drift = args.get("acknowledge_drift", False)

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

    # Doble confirmación ante drift git: si el local quedó detrás del remoto (o con
    # cambios sin commitear), indexar reflejaría una versión desactualizada del código.
    # Avisamos y exigimos acknowledge_drift=true para indexar de todos modos.
    # Si el proyecto es un directorio padre no-git, el drift se evalúa sobre sus
    # repos hijos de primer nivel (check_remote_status los devuelve en child_repos).
    git_status = git_client.check_remote_status(project["path"])
    repos = {"el repo": git_status}
    for name, s in git_status.get("child_repos", {}).items():
        repos[f"'{name}'"] = s
    motivos = []
    for label, s in repos.items():
        if s.get("behind", 0) > 0:
            motivos.append(
                f"{label} está {s['behind']} commit(s) DETRÁS de '{s.get('branch', 'origin')}' en el remoto"
            )
        if s.get("dirty", False):
            motivos.append(f"{label} tiene cambios sin commitear en el working tree")
    if not acknowledge_drift and motivos:
        return {
            "needs_confirmation": True,
            "project": project_name,
            "git_status": git_status,
            "warning": (
                "Drift git detectado: " + "; ".join(motivos) + ". "
                "El índice reflejaría una versión potencialmente desactualizada del código. "
                "Recomendado: hacer `git pull` (y/o commitear) y reintentar. "
                "Para indexar el estado local actual de todos modos, vuelve a llamar con acknowledge_drift=true."
            ),
        }

    files_indexed, file_list = indexer.index_project(project["id"], project["path"], incremental=incremental)

    return {
        "project": project_name,
        "files_indexed": files_indexed,
        "total_files": len(file_list),
        "mode": "incremental" if incremental else "full",
        "git_status": git_status,
        "drift_acknowledged": bool(acknowledge_drift) if motivos else False,
    }
