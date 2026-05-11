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
