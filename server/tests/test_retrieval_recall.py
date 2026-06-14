"""Capa 1 del harness de evaluación — recall de RETRIEVAL (determinista, gratis).

Indexa las fixtures (fake_backend / fake_frontend) en un ChromaDB efímero y, para
cada bug sembrado en expected.json, verifica que el archivo con el bug aparezca
entre los chunks recuperados con la query de su categoría.

NO usa DeepSeek ni Postgres: solo embeddings (deterministas) + Chroma en disco temp.
Mide chunking + embeddings + reranking. Si el recall baja al tocar cualquiera de esos,
este test lo detecta. La Capa 2 (¿el audit REPORTA el bug?) vive en eval/run_eval.py.

Se salta si sentence-transformers no está instalado o el modelo no puede cargar
(p. ej. CI sin red ni cache).
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("sentence_transformers")

import indexer  # noqa: E402
import retriever  # noqa: E402
from tools.audit_project import _BACKEND_MAP, _FRONTEND_MAP  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
# Umbral de recall mínimo: tolera 1 miss difuso pero atrapa regresiones reales.
MIN_RECALL = 0.70


def _load_files(root: Path) -> dict[str, str]:
    files = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root))
            files[rel] = p.read_text(encoding="utf-8")
    return files


def _build_ephemeral_index(tmp_path, files: dict[str, str], pid: int) -> None:
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    col = client.get_or_create_collection("code_chunks", metadata={"hnsw:space": "cosine"})
    # forzar que retriever use esta colección efímera
    indexer._chroma = client
    indexer._collection = col

    model = indexer._get_model()
    docs, ids, metas = [], [], []
    for rel, content in files.items():
        ext = Path(rel).suffix.lower()
        for i, (chunk, start_line) in enumerate(indexer._chunk_content(content, rel)):
            docs.append(chunk)
            ids.append(f"{pid}:{rel}:{i}")
            metas.append({
                "project_id": pid,
                "file_path": rel,
                "chunk_index": i,
                "start_line": start_line,
                "symbols": indexer._extract_symbols(chunk, ext),
            })
    embeddings = model.encode(docs, show_progress_bar=False).tolist()
    col.add(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)


def _expected():
    return json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def _run_side(tmp_path, side: str, pid: int, query_map: dict) -> tuple[int, int, list[str]]:
    try:
        model_ok = indexer._get_model() is not None
    except Exception as e:  # modelo no descargable
        pytest.skip(f"No se pudo cargar el modelo de embeddings: {e}")
    assert model_ok

    files = _load_files(FIXTURES / f"fake_{side}")
    _build_ephemeral_index(tmp_path / side, files, pid)

    expected = _expected()[side]
    hits, total, misses = 0, 0, []
    for bug in expected:
        total += 1
        query = query_map.get(bug["category"], bug["category"])
        results = retriever.retrieve(query, pid, top_k=12, code_only=True)
        retrieved = {c["file_path"] for c in results}
        if bug["file"] in retrieved:
            hits += 1
        else:
            misses.append(f'{bug["id"]} ({bug["file"]} no recuperado con query de {bug["category"]})')
    return hits, total, misses


def test_retrieval_recall_backend(tmp_path):
    hits, total, misses = _run_side(tmp_path, "backend", pid=9001, query_map=_BACKEND_MAP)
    recall = hits / total
    print(f"\nbackend retrieval recall: {hits}/{total} = {recall:.0%}")
    for m in misses:
        print("  MISS:", m)
    assert recall >= MIN_RECALL, f"recall backend {recall:.0%} < {MIN_RECALL:.0%}: {misses}"


def test_retrieval_recall_frontend(tmp_path):
    hits, total, misses = _run_side(tmp_path, "frontend", pid=9002, query_map=_FRONTEND_MAP)
    recall = hits / total
    print(f"\nfrontend retrieval recall: {hits}/{total} = {recall:.0%}")
    for m in misses:
        print("  MISS:", m)
    assert recall >= MIN_RECALL, f"recall frontend {recall:.0%} < {MIN_RECALL:.0%}: {misses}"
