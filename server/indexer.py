from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
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


def _file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _chunk_content(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)
    chunks = []
    start = 0
    while start < len(lines):
        end = min(start + CHUNK_SIZE, len(lines))
        chunks.append("".join(lines[start:end]))
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _make_chunk_id(project_id: int, rel_path: str, chunk_index: int) -> str:
    key = f"{project_id}:{rel_path}:{chunk_index}"
    return hashlib.sha1(key.encode()).hexdigest()


def _extract_imports(full_path: str, content: str, rel_path: str) -> list[dict]:
    """Extrae nombres importados de archivos Python y JS/TS."""
    ext = Path(full_path).suffix.lower()
    imports = []

    if ext == ".py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append({"file_path": rel_path, "import_name": alias.name})
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append({"file_path": rel_path, "import_name": node.module})
                    for alias in node.names:
                        if alias.name != "*":
                            imports.append({"file_path": rel_path, "import_name": alias.name})
        except SyntaxError:
            pass

    elif ext in {".js", ".ts", ".jsx", ".tsx"}:
        # import { X, Y } from 'module'
        for m in re.finditer(r"import\s*\{([^}]+)\}", content):
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                if name and name != "*":
                    imports.append({"file_path": rel_path, "import_name": name})
        # import X from 'module'
        for m in re.finditer(r"import\s+(\w+)\s+from\s+['\"]", content):
            imports.append({"file_path": rel_path, "import_name": m.group(1)})
        # require('module')
        for m in re.finditer(r"require\s*\(\s*['\"]([^'\"]+)['\"]", content):
            imports.append({"file_path": rel_path, "import_name": m.group(1)})

    return imports


def _delete_file_chunks(collection, project_id: int, rel_path: str) -> None:
    try:
        collection.delete(
            where={"$and": [{"project_id": {"$eq": project_id}}, {"file_path": {"$eq": rel_path}}]}
        )
    except Exception as e:
        logger.warning("No se pudo borrar chunks de %s: %s", rel_path, e)


def index_project(
    project_id: int,
    project_path: str,
    incremental: bool = False,
) -> tuple[int, list[str]]:
    collection = _get_collection()
    model = _get_model()

    existing_hashes: dict[str, str] = db.get_file_hashes(project_id) if incremental else {}

    if not incremental:
        collection.delete(where={"project_id": project_id})

    all_files_for_db: list[dict] = []
    new_imports: list[dict] = []
    changed_paths: list[str] = []

    new_chunks: list[str] = []
    new_ids: list[str] = []
    new_metadatas: list[dict] = []
    skipped = 0

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if not security.is_dir_blocked(d)]

        for filename in files:
            full_path = os.path.join(root, filename)
            allowed, reason = security.is_file_allowed(full_path)
            if not allowed:
                logger.debug("Ignorando %s: %s", full_path, reason)
                continue

            rel_path = os.path.relpath(full_path, project_path)

            try:
                file_hash = _file_hash(full_path)
            except OSError:
                continue

            if incremental and existing_hashes.get(rel_path) == file_hash:
                all_files_for_db.append({
                    "file_path": rel_path,
                    "file_size": os.path.getsize(full_path),
                    "content_hash": file_hash,
                })
                skipped += 1
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            chunks = _chunk_content(content)
            if not chunks:
                continue

            if incremental and rel_path in existing_hashes:
                _delete_file_chunks(collection, project_id, rel_path)

            all_files_for_db.append({
                "file_path": rel_path,
                "file_size": os.path.getsize(full_path),
                "content_hash": file_hash,
            })
            changed_paths.append(rel_path)
            new_imports.extend(_extract_imports(full_path, content, rel_path))

            for i, chunk in enumerate(chunks):
                new_chunks.append(chunk)
                new_ids.append(_make_chunk_id(project_id, rel_path, i))
                new_metadatas.append({
                    "project_id": project_id,
                    "file_path": rel_path,
                    "chunk_index": i,
                })

    # archivos eliminados del disco en modo incremental
    if incremental:
        current_paths = {f["file_path"] for f in all_files_for_db}
        for deleted_path in set(existing_hashes.keys()) - current_paths:
            _delete_file_chunks(collection, project_id, deleted_path)

    if new_chunks:
        logger.info("Generando embeddings para %d chunks...", len(new_chunks))
        embeddings = model.encode(new_chunks, show_progress_bar=False).tolist()

        batch = 500
        for i in range(0, len(new_chunks), batch):
            collection.add(
                ids=new_ids[i:i + batch],
                documents=new_chunks[i:i + batch],
                embeddings=embeddings[i:i + batch],
                metadatas=new_metadatas[i:i + batch],
            )

    db.log_indexed_files(project_id, all_files_for_db)

    if incremental:
        db.update_file_imports(project_id, changed_paths, new_imports)
    else:
        db.log_file_imports(project_id, new_imports)

    db.update_last_indexed(project_id)

    new_count = len(all_files_for_db) - skipped
    logger.info(
        "Proyecto %d: %d archivos nuevos/cambiados, %d sin cambios, %d chunks",
        project_id, new_count, skipped, len(new_chunks),
    )
    return new_count, [f["file_path"] for f in all_files_for_db]
