"""Regresion del indexado largo: no debe congelar el event loop y debe reportar
progreso monotono. Antes `indexer.index_project` (sincrono) corria dentro de un
`async def`: mientras indexaba, el server no atendia ninguna otra tool y no daba
senal de vida, y el cliente abortaba la llamada por inactividad."""

import asyncio
import time

import indexer
import progress


def test_run_index_no_bloquea_el_loop_y_reporta(monkeypatch):
    def fake_index(project_id, path, incremental=False, report=None):
        for pct in (10.0, 50.0, 90.0):
            time.sleep(0.1)  # trabajo sincrono: si corriera en el loop lo congelaria
            report(pct, f"fase {pct}")
        return 3, ["a", "b", "c"]

    monkeypatch.setattr(indexer, "index_project", fake_index)
    progress.JOBS.clear()

    async def _run():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        t = asyncio.create_task(ticker())
        task = asyncio.create_task(progress.run_index("demo", 1, "/x"))
        await asyncio.sleep(0.15)
        # a mitad del indexado: otra tool podria responder y el job figura en curso
        assert progress.busy_error("demo") is not None
        assert progress.snapshot("demo")["demo"]["status"] == "running"
        result = await task
        t.cancel()
        return result, ticks

    result, ticks = asyncio.run(_run())
    assert result == (3, ["a", "b", "c"])
    assert ticks >= 5, "el loop se congelo mientras indexaba"
    snap = progress.snapshot("demo")["demo"]
    assert snap["status"] == "done" and snap["percent"] == 100.0
    assert progress.busy_error("demo") is None


def test_run_index_marca_error_y_libera(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("chroma caido")

    monkeypatch.setattr(indexer, "index_project", boom)
    progress.JOBS.clear()

    async def _run():
        try:
            await progress.run_index("rota", 1, "/x")
        except RuntimeError:
            pass

    asyncio.run(_run())
    assert progress.snapshot("rota")["rota"]["status"] == "error"
    assert progress.busy_error("rota") is None  # un error no deja el proyecto bloqueado


def test_indexer_embebe_por_lotes_y_el_progreso_es_monotono(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x = 1\n" * 40, encoding="utf-8")

    added = []

    class FakeCollection:
        def delete(self, **k): pass
        def add(self, ids, documents, embeddings, metadatas): added.append(len(ids))

    class FakeVec(list):
        def tolist(self): return list(self)

    class FakeModel:
        def encode(self, chunks, show_progress_bar=False): return FakeVec([[0.0]] * len(chunks))

    monkeypatch.setattr(indexer, "_get_collection", lambda: FakeCollection())
    monkeypatch.setattr(indexer, "_get_model", lambda: FakeModel())
    monkeypatch.setattr(indexer, "_git_ignored_paths", lambda p: set())
    monkeypatch.setattr(indexer, "_EMBED_BATCH", 2)
    for fn in ("log_indexed_files", "log_file_imports", "update_last_indexed"):
        monkeypatch.setattr(indexer.db, fn, lambda *a, **k: None)
    monkeypatch.setattr(indexer.security, "is_file_allowed", lambda p: (True, ""))
    monkeypatch.setattr(indexer.security, "is_dir_blocked", lambda d: False)

    seen = []
    n, files = indexer.index_project(1, str(tmp_path), progress=lambda p, m: seen.append(p))

    assert n == 5 and len(files) == 5
    assert max(added) <= 2 and len(added) >= 2, "no se embebio por lotes"
    assert seen == sorted(seen), f"el progreso retrocedio: {seen}"
    assert seen[0] == 0.0 and seen[-1] < 100.0  # el 100 lo pone run_index al terminar
