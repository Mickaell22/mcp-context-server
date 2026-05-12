from __future__ import annotations

import logging
from pathlib import Path

from config import TOP_K_RESULTS, MAX_DISTANCE
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


def retrieve(
    query: str,
    project_ids: int | list[int],
    top_k: int = TOP_K_RESULTS,
    code_only: bool = False,
) -> list[dict]:
    """
    Retorna los chunks mas relevantes para la query.
    project_ids puede ser un int o lista de ints para queries cross-repo.
    code_only=True excluye archivos de documentacion (.md, .txt, etc).
    Cada item: {file_path, project_id, chunk_index, content, distance}
    """
    model = _get_model()
    collection = _get_collection()

    if isinstance(project_ids, int):
        project_ids = [project_ids]

    embedding = model.encode([query], show_progress_bar=False).tolist()[0]

    where = {"project_id": {"$in": project_ids}} if len(project_ids) > 1 else {"project_id": project_ids[0]}

    results = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
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
            "content": doc,
            "distance": dist,
        })

    logger.debug("Recuperados %d chunks para query: %s", len(chunks), query[:60])
    return chunks


def get_file_chunks(project_id: int, file_path: str) -> list[dict]:
    """Retorna todos los chunks de un archivo especifico en orden."""
    collection = _get_collection()

    results = collection.get(
        where={"project_id": project_id, "file_path": file_path},
        include=["documents", "metadatas"],
    )

    chunks = sorted(
        zip(results["documents"], results["metadatas"]),
        key=lambda x: x[1]["chunk_index"],
    )

    return [{"content": doc, "chunk_index": meta["chunk_index"]} for doc, meta in chunks]
