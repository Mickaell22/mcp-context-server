import logging
import os
from urllib.parse import urlparse
import git

from config import PROJECTS_BASE_PATH, GITHUB_TOKEN

logger = logging.getLogger(__name__)


# Prefijos de los tokens de GitHub (PAT clasico, fine-grained, OAuth, app...).
# Sirven para reconocer un token persistido por error en remote.origin.url.
_TOKEN_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")


def _redact(text: str) -> str:
    """Oculta el token en textos que se devuelven al cliente (git lo incluye
    en los mensajes de error junto a la URL completa)."""
    return text.replace(GITHUB_TOKEN, "***") if GITHUB_TOKEN else text


def _inject_token(repo_url: str) -> str:
    """URL con el token para autenticar contra GitHub. Solo en memoria: el
    resultado nunca se persiste en `.git/config` (ver `_clean_persisted_url`).

    Es idempotente: descarta el userinfo que la URL ya traiga, asi que aplicarlo
    sobre una URL ya tokenizada no acumula credenciales.
    """
    if not GITHUB_TOKEN:
        return repo_url
    parsed = urlparse(repo_url)
    host = parsed.netloc.rsplit("@", 1)[-1]  # descarta cualquier userinfo previo
    hostname = host.rsplit(":", 1)[0].strip("[]").lower()
    # solo inyectar token en repos de GitHub via HTTPS. Se compara el host exacto
    # (no `"github.com" in netloc`): un host tipo github.com.atacante.net no debe
    # recibir el token.
    if parsed.scheme not in ("http", "https"):
        return repo_url
    if hostname != "github.com" and not hostname.endswith(".github.com"):
        return repo_url
    authed = parsed._replace(netloc=f"{GITHUB_TOKEN}@{host}")
    return authed.geturl()


def _clean_persisted_url(origin) -> str:
    """Devuelve la URL del remoto sin credenciales y, si habia un token escrito
    en `.git/config`, lo borra de disco.

    Repara los repos afectados por el bug historico de acumulacion (un
    `set_url(_inject_token(...))` por indexado dejaba
    `https://ghp_x@ghp_x@github.com/...`, que ademas rompia el fetch).
    Un userinfo que no parezca token (ej. `https://usuario@github.com/...`) se
    respeta: no es cosa nuestra tocarlo.
    """
    url = origin.url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "@" not in parsed.netloc:
        return url
    userinfo = parsed.netloc.rsplit("@", 1)[0]
    looks_like_token = any(p in userinfo for p in _TOKEN_PREFIXES) or (
        bool(GITHUB_TOKEN) and GITHUB_TOKEN in userinfo
    )
    if not looks_like_token:
        return url
    clean = parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]).geturl()
    try:
        origin.set_url(clean)
        logger.warning(
            "Se elimino un token de GitHub persistido en remote.%s.url (%s). "
            "Considera rotarlo: estuvo en texto plano en .git/config.",
            origin.name, clean,
        )
    except Exception:
        pass  # si no se puede escribir, seguimos con la URL limpia en memoria
    return clean


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
        # El token se pasa como URL explicita al fetch: vive solo en memoria y
        # nunca se escribe en .git/config (era una fuga de secreto, y ademas se
        # acumulaba un token por llamada hasta romper el fetch).
        # El refspec replica lo que hace `git fetch origin` por defecto: sin el,
        # `git fetch <url>` solo actualiza FETCH_HEAD y refs/remotes/origin/*
        # quedaria viejo, falseando el calculo de behind/ahead.
        fetch_url = _inject_token(_clean_persisted_url(origin))
        try:
            repo.git.fetch(fetch_url, f"+refs/heads/*:refs/remotes/{origin.name}/*")
        except Exception as e:
            return {"is_git": True, "has_remote": True, "dirty": dirty, "fetch_error": _redact(str(e))}

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
        return {"is_git": True, "error": _redact(str(e))}


def local_state(repo_path: str) -> dict:
    """Estado local BARATO: sin red, solo lee el repo en disco.

    Retorna {is_git, dirty, last_commit (ISO), branch}. Sirve para saber si el
    INDICE quedo viejo respecto al repo (distinto de si el REPO quedo viejo
    respecto a GitHub, que necesita fetch y lo resuelve check_remote_status).
    Igual que check_remote_status, si el path es un directorio padre mira los
    repos hijos de primer nivel y agrega: dirty si alguno lo esta, y el commit
    mas reciente de todos.
    """
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        try:
            entries = sorted(os.scandir(repo_path), key=lambda e: e.name)
        except OSError:
            return {"is_git": False}
        children = [
            local_state(e.path)
            for e in entries
            if e.is_dir() and os.path.isdir(os.path.join(e.path, ".git"))
        ]
        commits = [c["last_commit"] for c in children if c.get("last_commit")]
        if not children:
            return {"is_git": False}
        return {
            "is_git": False,
            "dirty": any(c.get("dirty") for c in children),
            "last_commit": max(commits) if commits else None,
        }
    try:
        repo = git.Repo(repo_path)
        try:
            last_commit = repo.head.commit.committed_datetime.isoformat()
        except (ValueError, TypeError):  # repo sin commits
            last_commit = None
        try:
            branch = repo.active_branch.name
        except TypeError:  # HEAD detached
            branch = None
        return {
            "is_git": True,
            "dirty": repo.is_dirty(untracked_files=False),
            "last_commit": last_commit,
            "branch": branch,
        }
    except Exception as e:
        return {"is_git": True, "error": _redact(str(e))}


def pull_repo(repo_path: str) -> dict:
    """git pull --ff-only contra la URL autenticada explicita (el token vive solo
    en memoria, nunca se escribe en .git/config).

    --ff-only a proposito: si el historial divergio, falla en vez de fabricar un
    merge en el repo del usuario. Retorna {ok, output} o {ok: False, error}.
    """
    try:
        repo = git.Repo(repo_path)
        if not repo.remotes:
            return {"ok": False, "error": "el repo no tiene remoto"}
        origin = repo.remotes.origin
        auth_url = _inject_token(_clean_persisted_url(origin))
        try:
            branch = repo.active_branch.name
        except TypeError:
            return {"ok": False, "error": "HEAD detached: no se puede hacer pull"}
        output = repo.git.pull("--ff-only", auth_url, branch)
        return {"ok": True, "output": _redact(output)}
    except Exception as e:
        return {"ok": False, "error": _redact(str(e))}


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
        # pull contra la URL autenticada explicita: el token no se persiste en
        # .git/config (y de paso se limpia si quedo uno de una version anterior).
        _clean_persisted_url(origin)
        repo.git.pull(auth_url)
    else:
        logger.info("Clonando %s en %s...", repo_url, dest)
        os.makedirs(PROJECTS_BASE_PATH, exist_ok=True)
        repo = git.Repo.clone_from(auth_url, dest)
        # clone_from deja la URL de origen (con token) escrita en .git/config
        try:
            repo.remotes.origin.set_url(repo_url)
        except Exception:
            pass

    return name, dest
