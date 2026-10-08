"""Progreso de indexados largos.

Antes, `indexer.index_project` (sincrono y de minutos en repos grandes) se
llamaba directo dentro de un `async def`: congelaba el event loop, asi que el
servidor no atendia ninguna otra tool ni podia dar senal de vida, y Claude Code
abortaba la llamada tras 30 min de silencio (CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT)
aunque el indexado siguiera vivo.

Ahora el indexado corre en un hilo (`run_index`) y reporta avance por tres vias:
  - notificacion MCP de progreso, si el cliente mando `progressToken`; segun el
    protocolo cada notificacion cuenta como senal de vida del cliente;
  - log a stderr (visible en el log del server);
  - `JOBS`, que expone la tool `index_status` aunque el cliente no pinte nada.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from mcp.server.lowlevel.server import request_ctx

import indexer

logger = logging.getLogger(__name__)

# nombre de proyecto -> estado del ultimo indexado (en curso o terminado).
# En memoria a proposito: describe el proceso vivo, no un dato a persistir.
JOBS: dict[str, dict] = {}
_lock = threading.Lock()

# Se notifica si el avance supera 1 punto o pasaron 2 s desde el ultimo aviso:
# sin tope, un repo de miles de chunks inundaria el canal stdio.
_MIN_STEP = 1.0
_MIN_SECONDS = 2.0


def busy_error(name: str) -> dict | None:
    """Error listo para devolver si ya hay un indexado de `name` en curso.
    Dos indexados simultaneos del mismo proyecto se pisarian los chunks."""
    with _lock:
        job = JOBS.get(name)
        if job and job["status"] == "running":
            return {
                "error": (
                    f"'{name}' ya se esta indexando ({job['percent']}%). "
                    "Consulta el avance con index_status."
                )
            }
    return None


def snapshot(name: str | None = None) -> dict:
    """Copia del estado de los indexados, con tiempo transcurrido y ETA."""
    now = time.time()
    with _lock:
        names = [name] if name else list(JOBS)
        out = {}
        for n in names:
            job = JOBS.get(n)
            if not job:
                continue
            end = job.get("finished_at") or now
            elapsed = round(end - job["started_at"], 1)
            view = {k: job[k] for k in ("status", "percent", "message")}
            view["elapsed_s"] = elapsed
            # ponytail: ETA lineal sobre el porcentaje; la fase de embeddings
            # domina y es casi lineal, pero antes del 5 % no hay base para estimar.
            if job["status"] == "running" and job["percent"] >= 5:
                view["eta_s"] = round(elapsed * (100 - job["percent"]) / job["percent"], 0)
            out[n] = view
    return out


def _notify(ctx, loop: asyncio.AbstractEventLoop, percent: float, message: str) -> None:
    token = getattr(getattr(ctx, "meta", None), "progressToken", None)
    if ctx is None or token is None:
        return
    try:
        fut = asyncio.run_coroutine_threadsafe(
            ctx.session.send_progress_notification(token, percent, 100.0, message), loop
        )
        # Un fallo al notificar (cliente cerrado) no debe tumbar el indexado.
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
    except Exception as exc:
        # Tambien cubre un SDK `mcp` viejo (otro equipo/Linux) cuya firma de
        # send_progress_notification no acepte `message`: el progreso es un
        # extra, nunca debe romper el indexado.
        logger.debug("No se pudo enviar el progreso MCP: %s", exc)


async def run_index(name: str, project_id: int, path: str, incremental: bool = False):
    """Indexa en un hilo y reporta progreso. Devuelve lo mismo que
    `indexer.index_project`: (archivos_nuevos, lista_de_archivos)."""
    loop = asyncio.get_running_loop()
    try:
        ctx = request_ctx.get()
    except LookupError:  # fuera de una peticion MCP (tests, scripts)
        ctx = None

    job = {
        "status": "running", "percent": 0.0, "message": "Iniciando",
        "started_at": time.time(), "finished_at": None,
    }
    with _lock:
        JOBS[name] = job
    last = {"pct": -100.0, "t": 0.0}

    def report(percent: float, message: str) -> None:
        now = time.monotonic()
        with _lock:
            job["percent"] = round(percent, 1)
            job["message"] = message
        if percent < 100 and percent - last["pct"] < _MIN_STEP and now - last["t"] < _MIN_SECONDS:
            return
        last["pct"], last["t"] = percent, now
        logger.info("Indexando %s: %.0f%% - %s", name, percent, message)
        _notify(ctx, loop, percent, message)

    try:
        result = await asyncio.to_thread(indexer.index_project, project_id, path, incremental, report)
    except Exception as exc:
        with _lock:
            job.update(status="error", message=str(exc), finished_at=time.time())
        raise
    report(100.0, "Terminado")
    with _lock:
        job.update(status="done", finished_at=time.time())
    return result
