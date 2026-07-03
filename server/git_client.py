import logging
import os
from urllib.parse import urlparse
import git

from config import PROJECTS_BASE_PATH, GITHUB_TOKEN

logger = logging.getLogger(__name__)


def _inject_token(repo_url: str) -> str:
    if not GITHUB_TOKEN:
        return repo_url
    parsed = urlparse(repo_url)
    # solo inyectar token en repos de GitHub via HTTPS
    if parsed.scheme not in ("http", "https") or "github.com" not in parsed.netloc:
        return repo_url
    authed = parsed._replace(netloc=f"{GITHUB_TOKEN}@{parsed.netloc}")
    return authed.geturl()


def extract_repo_name(repo_url: str) -> str:
    path = urlparse(repo_url).path.rstrip("/")
    name = os.path.basename(path)
    if name.endswith(".git"):
        name = name[:-4]
    return name


def check_remote_status(repo_path: str) -> dict:
    """Compara el working tree local con su remoto (hace fetch, no pull).

    Sirve para detectar el caso 'el local quedó desactualizado respecto a GitHub':
    si se indexa así, el índice refleja una versión vieja del código.

    Retorna un dict con: is_git, has_remote, behind (commits detrás del remoto),
    ahead, dirty (cambios sin commitear), branch, y opcionalmente fetch_error/error.
    No lanza: ante cualquier fallo devuelve la info parcial que pudo obtener.
    """
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        # Un proyecto puede ser un directorio padre con repos hijos de primer nivel
        # (ej. EcuaInventario/ con Backend/ y Frontend/): el drift vive en los hijos.
        children: dict[str, dict] = {}
        try:
            entries = sorted(os.scandir(repo_path), key=lambda e: e.name)
        except OSError:
            return {"is_git": False}
        for entry in entries:
            if entry.is_dir() and os.path.isdir(os.path.join(entry.path, ".git")):
                children[entry.name] = check_remote_status(entry.path)
        if children:
            return {"is_git": False, "child_repos": children}
        return {"is_git": False}
    try:
        repo = git.Repo(repo_path)
        dirty = repo.is_dirty(untracked_files=False)

        if not repo.remotes:
            return {"is_git": True, "has_remote": False, "dirty": dirty}

        origin = repo.remotes.origin
        # inyectar token para poder hacer fetch de repos privados
        try:
            origin.set_url(_inject_token(origin.url))
        except Exception:
            pass
        try:
            origin.fetch()
        except Exception as e:
            return {"is_git": True, "has_remote": True, "dirty": dirty, "fetch_error": str(e)}

        try:
            branch = repo.active_branch
        except TypeError:  # HEAD detached
            return {"is_git": True, "has_remote": True, "dirty": dirty, "detached": True}

        tracking = branch.tracking_branch()
        if tracking is None:
            return {"is_git": True, "has_remote": True, "dirty": dirty, "branch": branch.name, "tracking": False}

        behind = sum(1 for _ in repo.iter_commits(f"{branch.name}..{tracking.name}"))
        ahead = sum(1 for _ in repo.iter_commits(f"{tracking.name}..{branch.name}"))
        return {
            "is_git": True,
            "has_remote": True,
            "dirty": dirty,
            "branch": branch.name,
            "behind": behind,
            "ahead": ahead,
        }
    except Exception as e:
        return {"is_git": True, "error": str(e)}


def clone_repo(repo_url: str) -> tuple[str, str]:
    """
    Clona el repo en PROJECTS_BASE_PATH/<nombre>.
    Retorna (nombre_proyecto, path_absoluto).
    Si ya existe, hace git pull en lugar de clonar.
    """
    name = extract_repo_name(repo_url)
    dest = os.path.join(PROJECTS_BASE_PATH, name)
    auth_url = _inject_token(repo_url)

    if os.path.isdir(os.path.join(dest, ".git")):
        logger.info("Repo ya existe en %s, haciendo pull...", dest)
        repo = git.Repo(dest)
        origin = repo.remotes.origin
        # actualizar la URL con token por si cambio
        origin.set_url(auth_url)
        origin.pull()
    else:
        logger.info("Clonando %s en %s...", repo_url, dest)
        os.makedirs(PROJECTS_BASE_PATH, exist_ok=True)
        git.Repo.clone_from(auth_url, dest)

    return name, dest
