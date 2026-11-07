"""Regresion: la primera query_context se quedaba 30 min colgada sin decir donde.

Si la recuperacion (modelo/Chroma) no vuelve, la tool debe fallar en
RETRIEVAL_TIMEOUT con la etapa en el mensaje, y sin bloquear el event loop.
Offline: db, security y retriever van monkeypatcheados.
"""

import asyncio
import threading

import indexer
from tools import query_context


def test_query_context_corta_un_retrieve_colgado(monkeypatch):
    liberar = threading.Event()
    monkeypatch.setattr(query_context.db, "get_project_by_name", lambda n: {"id": 1, "name": n, "path": "/x"})
    monkeypatch.setattr(query_context.security, "is_path_allowed", lambda p: True)
    monkeypatch.setattr(query_context.retriever, "retrieve", lambda *a, **k: liberar.wait(5))
    monkeypatch.setattr(query_context, "RETRIEVAL_TIMEOUT", 0.2)
    monkeypatch.setattr(indexer, "init_stage", "cargando modelo X")

    async def run():
        # El loop sigue vivo mientras el retrieve esta colgado: otro coroutine avanza.
        latido = asyncio.create_task(asyncio.sleep(0.05))
        out = await query_context.handle({"query": "q", "project": "p"}, None)
        assert latido.done()
        return out

    try:
        out = asyncio.run(run())
    finally:
        liberar.set()
    assert "error" in out
    assert "cargando modelo X" in out["error"]
