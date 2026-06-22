from __future__ import annotations

import logging
import re
from pathlib import Path

from config import (
    TOP_K_RESULTS, MAX_DISTANCE,
    RERANK_ENABLED, RERANK_CANDIDATE_MULT, RERANK_W_SEM, RERANK_W_LEX,
)
from indexer import _get_model, _get_collection

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cs", ".go", ".rs",
    ".dart", ".kt", ".swift", ".rb", ".php",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
    ".html", ".css", ".scss", ".sass",
    ".sql", ".sh", ".bash",
}

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# Stopwords ES/EN frecuentes en queries NL que no aportan señal léxica sobre código.
_STOPWORDS = {
    "the", "and", "for", "with", "que", "los", "las", "una", "del", "con",
    "como", "donde", "esta", "este", "por", "para", "logica", "codigo",
    "problemas", "relacionados", "busca", "categoria",
}


def _lexical_score(query: str, content: str) -> float:
    """Fracción de tokens significativos de la query presentes en el chunk."""
    q = {t.lower() for t in _TOKEN_RE.findall(query)} - _STOPWORDS
    if not q:
        return 0.0
    low = content.lower()
    return sum(1 for t in q if t in low) / len(q)


def _rerank(query: str, chunks: list[dict]) -> list[dict]:
    """Reordena por score mixto: semántico (1 - dist/MAX_DISTANCE) + léxico."""
    for c in chunks:
        sem = 1.0 - min(c["distance"], MAX_DISTANCE) / MAX_DISTANCE
        lex = _lexical_score(query, c["content"])
        c["_score"] = RERANK_W_SEM * sem + RERANK_W_LEX * lex
    return sorted(chunks, key=lambda c: c["_score"], reverse=True)


def retrieve(
    query: str,
    project_ids: int | list[int],
    top_k: int = TOP_K_RESULTS,
    code_only: bool = False,
    rerank: bool = True,
) -> list[dict]:
    """
    Retorna los chunks mas relevantes para la query.
    project_ids puede ser un int o lista de ints para queries cross-repo.
    code_only=True excluye archivos de documentacion (.md, .txt, etc).
    rerank=True recupera un pool mayor y lo reordena con score híbrido sem+léxico.
    Cada item: {file_path, project_id, chunk_index, start_line, end_line, symbols, content, distance}
    """
    model = _get_model()
    collection = _get_collection()

    if isinstance(project_ids, int):
        project_ids = [project_ids]

    embedding = model.encode([query], show_progress_bar=False).tolist()[0]

    where = {"project_id": {"$in": project_ids}} if len(project_ids) > 1 else {"project_id": project_ids[0]}

    do_rerank = rerank and RERANK_ENABLED
    n_results = top_k * RERANK_CANDIDATE_MULT if do_rerank else top_k

    results = collection.query(
        query_embeddings=[embedding],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if dist > MAX_DISTANCE:
            continue

        if code_only:
            ext = Path(meta["file_path"]).suffix.lower()
            if ext not in CODE_EXTENSIONS:
                continue

        chunks.append({
            "file_path": meta["file_path"],
            "project_id": meta["project_id"],
            "chunk_index": meta["chunk_index"],
            "start_line": meta.get("start_line"),
            "end_line": meta.get("end_line"),
            "symbols": meta.get("symbols", ""),
            "content": doc,
            "distance": dist,
        })

    if do_rerank and chunks:
        chunks = _rerank(query, chunks)

    chunks = chunks[:top_k]
    logger.debug("Recuperados %d chunks para query: %s", len(chunks), query[:60])
    return chunks


def get_file_chunks(project_id: int, file_path: str) -> list[dict]:
    """Retorna todos los chunks de un archivo especifico en orden."""
    collection = _get_collection()

    results = collection.get(
        where={"$and": [{"project_id": {"$eq": project_id}}, {"file_path": {"$eq": file_path}}]},
        include=["documents", "metadatas"],
    )

    chunks = sorted(
        zip(results["documents"], results["metadatas"]),
        key=lambda x: x[1]["chunk_index"],
    )

    return [
        {
            "content": doc,
            "chunk_index": meta["chunk_index"],
            "start_line": meta.get("start_line"),
            "end_line": meta.get("end_line"),
            "symbols": meta.get("symbols", ""),
        }
        for doc, meta in chunks
    ]
