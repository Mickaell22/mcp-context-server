"""Actualizacion del PROPIO servidor MCP desde GitHub.

Distinto de tools/check_updates.py, que mira los proyectos indexados: aca el
sujeto es el codigo que esta corriendo ahora mismo. Caso de uso: se toca el
server en la laptop y se sube a GitHub, y en la PC del trabajo hay que enterarse
de que hay version nueva sin ir a mirar el repo a mano.

El repo se resuelve desde __file__, NO desde la base de datos: en un equipo nuevo
el server puede no estar registrado como proyecto indexado, pero siempre sabe
donde vive su propio codigo.

Nunca se reinicia solo: cambiar los .py en disco no afecta al proceso vivo (los
modulos ya estan en memoria), asi que despues de actualizar hay que reiniciar
Claude Code. Un proceso stdio que se mata a si mismo dejaria la sesion colgada.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

import git

import git_client

logger = logging.getLogger(__name__)

# server/self_update.py -> server/ -> raiz del repo
REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO_ROOT / "server" / "requirements.txt"

# Cada cuanto se vuelve a preguntar a GitHub. El aviso viaja en list_projects, que
# se llama al entrar a cualquier proyecto: sin este tope seria un fetch por llamada.
CHECK_TTL_SECONDS = 6 * 3600
_cache: tuple[float, dict] | None = None


def _repo() -> git.Repo:
    return git.Repo(REPO_ROOT)


def local_version() -> dict:
    """Version que esta corriendo AHORA (sin red)."""
    try:
        repo = _repo()
        head = repo.head.commit
        return {
            "commit": head.hexsha[:8],
            "date": head.committed_datetime.isoformat(),
            "subject": head.summary,
            "branch": repo.active_branch.name if not repo.head.is_detached else None,
            "dirty": repo.is_dirty(untracked_files=False),
        }
    except Exception as e:
        return {"error": git_client._redact(str(e))}


def check(force: bool = False) -> dict:
    """Compara el codigo local con el remoto. Cachea CHECK_TTL_SECONDS.

    Nunca lanza: si no hay red o el repo no tiene remoto, devuelve el motivo y
    update_available=False. Que falle un chequeo de version no puede tumbar una
    consulta de contexto.
    """
    global _cache
    if not force and _cache and (time.time() - _cache[0]) < CHECK_TTL_SECONDS:
        return _cache[1]

    result: dict = {"update_available": False, "local": local_version()}
    try:
        repo = _repo()
        if not repo.remotes:
            result["note"] = "el repo del server no tiene remoto configurado"
            _cache = (time.time(), result)
            return result

        origin = repo.remotes.origin
        fetch_url = git_client._inject_token(git_client._clean_persisted_url(origin))
        repo.git.fetch(fetch_url, f"+refs/heads/*:refs/remotes/{origin.name}/*")

        branch = repo.active_branch
        tracking = branch.tracking_branch()
        if tracking is None:
            result["note"] = f"la rama {branch.name} no sigue a ninguna rama remota"
            _cache = (time.time(), result)
            return result

        nuevos = list(repo.iter_commits(f"{branch.name}..{tracking.name}"))
        result["behind"] = len(nuevos)
        result["ahead"] = sum(1 for _ in repo.iter_commits(f"{tracking.name}..{branch.name}"))
        result["update_available"] = bool(nuevos)
        # Que trae la version nueva, para decidir si vale la pena reiniciar ahora
        result["commits"] = [
            {"commit": c.hexsha[:8], "subject": c.summary, "date": c.committed_datetime.isoformat()}
            for c in nuevos[:15]
        ]
        if nuevos:
            result["hint"] = (
                "Hay version nueva del server. Corre check_server_version(update=true) "
                "y reinicia Claude Code para cargarla."
            )
    except Exception as e:
        result["error"] = git_client._redact(str(e))

    _cache = (time.time(), result)
    return result


def _deps_changed(repo: git.Repo, desde: str, hasta: str) -> bool:
    """True si requirements.txt cambio entre dos commits."""
    try:
        cambiados = repo.git.diff("--name-only", desde, hasta).splitlines()
    except Exception:
        return True  # ante la duda, reinstalar: es idempotente y barato
    return any(f.endswith("requirements.txt") for f in cambiados)


def _install_deps() -> dict:
    """pip install en el venv del propio server.

    sys.executable ES el interprete del venv desde el que corre este proceso, asi
    que no hay que adivinar rutas ni activar nada. Es la unica ejecucion de un
    comando que no es git en todo el server, y solo ocurre bajo update=true y solo
    si el commit nuevo toco requirements.txt.
    """
    if not REQUIREMENTS.is_file():
        return {"ok": False, "error": f"no existe {REQUIREMENTS}"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS), "-q"],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-800:]}
        return {"ok": True, "output": (proc.stdout or "dependencias al dia").strip()[-400:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pip install supero los 10 minutos"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def apply_update() -> dict:
    """git pull --ff-only del propio repo + pip install si cambiaron las deps."""
    global _cache
    estado = check(force=True)
    if estado.get("error"):
        return {"ok": False, "error": estado["error"], "step": "check"}
    if not estado.get("update_available"):
        return {"ok": True, "updated": False, "message": "el server ya esta en la ultima version", **estado}

    repo = _repo()
    if repo.is_dirty(untracked_files=False):
        return {
            "ok": False,
            "updated": False,
            "error": "el repo del server tiene cambios sin commitear: no se hace pull para no pisarlos. "
                     "Commitealos o descartalos y reintenta.",
        }

    antes = repo.head.commit.hexsha
    pull = git_client.pull_repo(str(REPO_ROOT))
    if not pull.get("ok"):
        return {"ok": False, "updated": False, "error": pull.get("error"), "step": "pull"}

    repo = _repo()  # el HEAD cambio
    despues = repo.head.commit.hexsha
    out: dict = {
        "ok": True,
        "updated": True,
        "from": antes[:8],
        "to": despues[:8],
        "pull": pull.get("output", ""),
    }

    if _deps_changed(repo, antes, despues):
        out["deps"] = _install_deps()
        if not out["deps"].get("ok"):
            out["ok"] = False
            out["warning"] = (
                "El codigo se actualizo pero la instalacion de dependencias fallo. "
                "NO reinicies hasta correr: .venv/bin/pip install -r server/requirements.txt"
            )
    else:
        out["deps"] = {"ok": True, "skipped": "requirements.txt no cambio"}

    _cache = None  # el estado quedo viejo
    if out["ok"]:
        out["next_step"] = (
            "REINICIA Claude Code para cargar la version nueva: este proceso sigue "
            "ejecutando el codigo viejo que ya tenia en memoria."
        )
    return out
