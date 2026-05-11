import logging
from sentence_transformers import SentenceTransformer
import chromadb

from config import CHROMA_PERSIST_PATH, CHROMA_COLLECTION, EMBEDDING_MODEL, TOP_K_RESULTS
from indexer import _get_model, _get_collection

logger = logging.getLogger(__name__)


def retrieve(query: str, project_id: int, top_k: int = TOP_K_RESULTS) -> list[dict]:
    """
    Retorna los chunks mas relevantes para la query dentro del proyecto.
    Cada item: {file_path, chunk_index, content, distance}
    """
    model = _get_model()
    collection = _get_collection()

    embedding = model.encode([query], show_progress_bar=False).tolist()[0]

    results = collection.query(
        query_embeddings=[embedding],
        n_results=top_k,
        where={"project_id": project_id},
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "file_path": meta["file_path"],
            "chunk_index": meta["chunk_index"],
            "content": doc,
            "distance": dist,
        })

    logger.debug("Recuperados %d chunks para query: %s", len(chunks), query[:60])
    return chunks
