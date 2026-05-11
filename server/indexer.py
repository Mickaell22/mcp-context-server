from __future__ import annotations

import os
import logging
from pathlib import Path
from sentence_transformers import SentenceTransformer
import chromadb

import db
import security
from config import (
    CHROMA_PERSIST_PATH,
    CHROMA_COLLECTION,
    EMBEDDING_MODEL,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_chroma: chromadb.PersistentClient | None = None
_collection = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Cargando modelo de embeddings: %s", EMBEDDING_MODEL)
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _get_collection():
    global _chroma, _collection
    if _collection is None:
        _chroma = chromadb.PersistentClient(path=CHROMA_PERSIST_PATH)
        _collection = _chroma.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _chunk_file(file_path: str) -> list[str]:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return []

    chunks = []
    start = 0
    while start < len(lines):
        end = min(start + CHUNK_SIZE, len(lines))
        chunks.append("".join(lines[start:end]))
        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


def index_project(project_id: int, project_path: str) -> tuple[int, list[str]]:
    collection = _get_collection()
    model = _get_model()

    # limpiar chunks anteriores del proyecto
    collection.delete(where={"project_id": project_id})

    indexed_files = []
    all_chunks: list[str] = []
    all_ids: list[str] = []
    all_metadatas: list[dict] = []
    chunk_counter = 0

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not security.is_dir_blocked(d)]

        for filename in files:
            full_path = os.path.join(root, filename)
            allowed, reason = security.is_file_allowed(full_path)
            if not allowed:
                logger.debug("Ignorando %s: %s", full_path, reason)
                continue

            rel_path = os.path.relpath(full_path, project_path)
            file_size = os.path.getsize(full_path)
            chunks = _chunk_file(full_path)

            if not chunks:
                continue

            indexed_files.append({"file_path": rel_path, "file_size": file_size})

            for i, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_ids.append(f"{project_id}_{chunk_counter}")
                all_metadatas.append({
                    "project_id": project_id,
                    "file_path": rel_path,
                    "chunk_index": i,
                })
                chunk_counter += 1

    if all_chunks:
        logger.info("Generando embeddings para %d chunks...", len(all_chunks))
        embeddings = model.encode(all_chunks, show_progress_bar=False).tolist()

        # insertar en lotes de 500 para no saturar memoria
        batch = 500
        for i in range(0, len(all_chunks), batch):
            collection.add(
                ids=all_ids[i:i+batch],
                documents=all_chunks[i:i+batch],
                embeddings=embeddings[i:i+batch],
                metadatas=all_metadatas[i:i+batch],
            )

    db.log_indexed_files(project_id, indexed_files)
    db.update_last_indexed(project_id)

    file_paths = [f["file_path"] for f in indexed_files]
    logger.info("Indexados %d archivos, %d chunks para proyecto %d", len(indexed_files), chunk_counter, project_id)
    return len(indexed_files), file_paths
