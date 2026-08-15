"""Frescura de los proyectos: repo vs GitHub, e indice vs repo.

Son dos ejes distintos que se confunden facil:
  - el REPO local puede estar detras de GitHub  -> hace falta git pull (necesita red)
  - el INDICE de Chroma puede estar detras del repo -> hace falta index_project

check_remote_status ya sabia calcular el primero pero solo se usaba como gate
dentro de index_project; aca se expone como consulta directa sobre todos los
proyectos de este equipo, y se combina con el segundo.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import db
import git_client
from config import DEVICE_ID
from tools import index_project

logger = logging.getLogger(__name__)


def _as_aware(iso: str | None) -> datetime | None:
    """Fecha ISO -> datetime con timezone, para poder comparar peras con peras.

    device_indexed_at se guarda con datetime.now().isoformat(): NAIVE y en hora
    local del equipo. Git devuelve committed_datetime AWARE con el offset del
    commit. Comparar los strings directamente da resultados falsos (un commit
    '2026-07-26T12:22:57-04:00' parece posterior a un indexado
    '2026-07-26T12:18:51' por texto y tambien por reloj, pero en otro repo el
    signo se invierte). astimezone() sobre un naive lo interpreta como hora
    local, que es exactamente como se escribio.
    """
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone()
    except (ValueError, TypeError):
        return None


def is_index_stale(local: dict, last_indexed: str | None) -> bool:
    """True si el codigo tiene commits posteriores al ultimo indexado de ESTE
    equipo. `local` es lo que devuelve git_client.local_state (sin red), asi que
    tambien lo usa list_projects para avisar sin costo."""
    indexed_at = _as_aware(last_indexed)
    if indexed_at is None:
        return True  # nunca indexado por este equipo
    committed_at = _as_aware(local.get("last_commit"))
    if committed_at is None:
        return False  # sin commits con que comparar
    return committed_at > indexed_at


def _inspect(path: str, last_indexed: str | None) -> dict:
    """Estado completo de un proyecto. Bloqueante (hace fetch por red): se llama
    desde un hilo."""
    remote = git_client.check_remote_status(path)
    local = git_client.local_state(path)
    index_stale = is_index_stale(local, last_indexed)

    return {
        "branch": remote.get("branch") or local.get("branch"),
        "behind": remote.get("behind", 0),
        "ahead": remote.get("ahead", 0),
        "dirty": bool(remote.get("dirty") or local.get("dirty")),
        "index_stale": index_stale,
        "last_commit": local.get("last_commit"),
        "last_indexed_here": last_indexed,
        "is_git": remote.get("is_git", False),
        # Todo esto funciona en CUALQUIER rama (se usa la activa, no main/master),
        # pero una rama sin upstream no tiene contra que compararse: behind/ahead
        # salen 0 y sin esta marca se reportaria como "al dia", que es mentira.
        # `comparable` distingue "no hay nada nuevo" de "no se puede saber".
        "comparable": remote.get("tracking", True) and not remote.get("detached"),
        "detached": bool(remote.get("detached")),
        "child_repos": remote.get("child_repos"),
        "fetch_error": remote.get("fetch_error") or remote.get("error"),
    }


def _child_problems(children: dict) -> list[str]:
    """Drift de los repos hijos, por nombre. Un proyecto puede estar registrado
    como directorio padre con los repos adentro (caso real: EcuaInventario/ con
    Backend/ y Frontend/): ahi el drift vive en los hijos y en la raiz no se ve."""
    out = []
    for nombre, st in sorted(children.items()):
        detalle = []
        if st.get("behind"):
            detalle.append(f"{st['behind']} detras")
        if st.get("ahead"):
            detalle.append(f"{st['ahead']} sin pushear")
        if st.get("dirty"):
            detalle.append("sin commitear")
        if detalle:
            out.append(f"{nombre}: {', '.join(detalle)}")
    return out


def _describe(st: dict) -> tuple[str, str]:
    """(status, action) legibles a partir del estado crudo."""
    if not st["is_git"] and not st.get("child_repos"):
        if st["index_stale"]:
            return "indice desactualizado", "index_project(incremental=true)"
        return "al dia", "nada"

    problems = []
    actions = []
    hijos = _child_problems(st.get("child_repos") or {})
    if hijos:
        problems.append("repos hijos con cambios (" + "; ".join(hijos) + ")")
        actions.append("git pull en cada repo hijo")
    if st.get("detached"):
        problems.append("HEAD detached: no hay rama con que comparar")
    elif not st.get("comparable", True):
        rama = st.get("branch") or "la rama actual"
        problems.append(f"la rama '{rama}' no sigue a ninguna remota: no se puede comparar con GitHub")
        actions.append(f"git push -u origin {rama}")
    if st["behind"]:
        problems.append(f"{st['behind']} commit(s) detras de GitHub")
        actions.append("git pull")
    if st["ahead"]:
        problems.append(f"{st['ahead']} commit(s) sin pushear")
    if st["dirty"]:
        problems.append("cambios sin commitear")
    if st["index_stale"]:
        problems.append("indice detras del codigo")
        actions.append("index_project(incremental=true)")
    if st.get("fetch_error"):
        problems.append("no se pudo consultar el remoto")

    if not problems:
        return "al dia", "nada"
    return "; ".join(problems), " + ".join(actions) if actions else "nada (solo informativo)"


async def _sync_one(name: str, path: str, st: dict) -> dict:
    """Pone al dia un proyecto. El git pull SOLO cuando el fast-forward es seguro:
    con cambios sin commitear o commits locales sin pushear no se toca el repo
    (el usuario esta trabajando ahi). El reindexado en cambio es solo lectura, asi
    que se hace igual si el indice quedo viejo."""
    done: dict = {}

    if st["behind"]:
        if st["dirty"] or st["ahead"]:
            motivo = "cambios sin commitear" if st["dirty"] else "commits locales sin pushear"
            done["pull"] = {"ok": False, "skipped": f"no se hizo pull: {motivo}. Resolvelo vos y reintenta"}
        elif not st["is_git"]:
            done["pull"] = {"ok": False, "skipped": "el path no es un repo git (mira child_repos)"}
        else:
            done["pull"] = await asyncio.to_thread(git_client.pull_repo, path)

    pulled = done.get("pull", {}).get("ok", False)
    if pulled or st["index_stale"]:
        done["reindex"] = await index_project.handle(
            {"project": name, "incremental": True, "acknowledge_drift": True}, None
        )
    return done


async def handle(args: dict, session_id: int | None) -> dict:
    only = (args.get("project") or "").strip()
    sync = bool(args.get("sync", False))

    projects = db.get_all_projects()
    if only:
        projects = [p for p in projects if p["name"] == only]
        if not projects:
            return {"error": f"Proyecto '{only}' no encontrado"}

    targets: list[tuple[dict, str]] = []
    not_here: list[str] = []
    for p in projects:
        path = db.resolve_project_path(p, DEVICE_ID)
        if path and os.path.isdir(path):
            targets.append((p, path))
        else:
            not_here.append(p["name"])

    if not targets:
        return {
            "device_id": DEVICE_ID,
            "checked": 0,
            "not_on_this_device": not_here,
            "note": "ningun proyecto tiene ruta local en este equipo (registralos con register_project)",
        }

    # Los fetch son I/O de red: en serie sobre decenas de proyectos la tool seria
    # inusable. Mismo patron que la pool del audit.
    states = await asyncio.gather(
        *(
            asyncio.to_thread(
                _inspect, path, (p.get("device_indexed_at") or {}).get(DEVICE_ID)
            )
            for p, path in targets
        )
    )

    report = []
    needs_attention = []
    for (p, path), st in zip(targets, states):
        status, action = _describe(st)
        entry = {"project": p["name"], "path": path, "status": status, "action": action, **st}
        if not st.get("child_repos"):
            entry.pop("child_repos", None)
        if not st.get("fetch_error"):
            entry.pop("fetch_error", None)

        # El sync va en serie a proposito (a diferencia de los fetch): reindexar
        # es CPU (embeddings) y disco, solaparlo no acelera nada y compite consigo mismo.
        if sync and status != "al dia":
            hecho = await _sync_one(p["name"], path, st)
            if hecho:  # un dict vacio solo dice "no habia nada que hacer aqui"
                entry["synced"] = hecho

        if status != "al dia":
            needs_attention.append(p["name"])
        report.append(entry)

    return {
        "device_id": DEVICE_ID,
        "checked": len(report),
        "needs_attention": needs_attention,
        "not_on_this_device": not_here,
        "synced": sync,
        "projects": report,
    }
