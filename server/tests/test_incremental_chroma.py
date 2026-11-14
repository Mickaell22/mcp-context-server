"""El incremental no confia en un hash de Postgres si el archivo no tiene chunks
en el Chroma local (regresion: tras migrar de equipo con el mismo DEVICE_ID, el
indice local quedo con 8 de 57 archivos y el incremental decia "sin cambios").
Offline: Chroma efimero, db monkeypatcheado."""
from __future__ import annotations

import pytest

pytest.importorskip("sentence_transformers")

import indexer as ix  # noqa: E402


def test_incremental_reindexa_archivos_sin_chunks_locales(tmp_path, monkeypatch):
    import chromadb

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n")
    (repo / "b.py").write_text("def b():\n    return 2\n")

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    monkeypatch.setattr(ix, "_chroma", client)
    monkeypatch.setattr(ix, "_collection", client.get_or_create_collection("code_chunks", metadata={"hnsw:space": "cosine"}))
    monkeypatch.setattr(ix, "_git_ignored_paths", lambda path: set())
    monkeypatch.setattr(ix.security, "is_file_allowed", lambda p: (True, ""))

    pid = 987654
    # Postgres "recuerda" los dos archivos con su hash actual...
    hashes = {"a.py": ix._file_hash(str(repo / "a.py")), "b.py": ix._file_hash(str(repo / "b.py"))}
    monkeypatch.setattr(ix.db, "get_file_hashes", lambda project_id: dict(hashes))
    for name in ("log_indexed_files", "update_file_imports", "update_last_indexed"):
        monkeypatch.setattr(ix.db, name, lambda *a, **k: None)

    # ...pero en el Chroma local solo esta a.py.
    dim = ix._get_model().get_sentence_embedding_dimension()
    ix._collection.add(ids=["x"], documents=["def a(): return 1"], embeddings=[[0.1] * dim],
                       metadatas=[{"project_id": pid, "file_path": "a.py", "chunk_index": 0}])

    count, files = ix.index_project(pid, str(repo), incremental=True)

    assert count == 1, "b.py no tenia chunks locales: tenia que re-indexarse"
    got = {m["file_path"] for m in ix._collection.get(where={"project_id": pid}, include=["metadatas"])["metadatas"]}
    assert got == {"a.py", "b.py"}
