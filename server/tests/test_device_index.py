"""Aislamiento por dispositivo del estado de indexado.

Los vectores viven en un ChromaDB LOCAL a cada equipo, pero `indexed_files`
vive en una Postgres COMPARTIDA. Sin separar por device_id, el equipo A marcaba
los archivos como indexados y el equipo B se saltaba el reindexado creyendolos
al dia: su Chroma quedaba vacio (o con codigo viejo) y las queries devolvian
nada sin avisar. Este test fija ese contrato.

Verifica SQL real, asi que necesita una Postgres de verdad. La suite es offline
por diseño (ver conftest), de modo que esto es OPT-IN: se salta salvo que se
exporte MCP_TEST_DATABASE_URL. Crea y borra su propio proyecto descartable.

    MCP_TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m pytest tests/test_device_index.py
"""

from __future__ import annotations

import os
import uuid

import pytest

_TEST_DB = os.environ.get("MCP_TEST_DATABASE_URL")
if not _TEST_DB:
    pytest.skip("define MCP_TEST_DATABASE_URL para correr este test", allow_module_level=True)

import db

# conftest deja una DATABASE_URL dummy; apuntamos a la base real solo aca.
db.DATABASE_URL = _TEST_DB


@pytest.fixture()
def proyecto():
    """Proyecto descartable en la DB compartida; se borra al terminar."""
    nombre = f"__test_device_{uuid.uuid4().hex[:8]}"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (name, path) VALUES (%s, %s) RETURNING id",
            (nombre, f"/tmp/{nombre}"),
        )
        pid = cur.fetchone()["id"]
    yield pid
    with db.cursor() as cur:
        cur.execute("DELETE FROM indexed_files WHERE project_id = %s", (pid,))
        cur.execute("DELETE FROM projects WHERE id = %s", (pid,))


def _archivos(*nombres):
    return [{"file_path": n, "file_size": 10, "content_hash": f"hash-{n}"} for n in nombres]


def test_hashes_aislados_por_dispositivo(proyecto):
    db.log_indexed_files(proyecto, _archivos("a.py"), device_id="equipoA")

    # El equipo B no hereda los hashes de A: su Chroma esta vacio, asi que su
    # proximo incremental debe re-indexar todo en vez de saltarselo.
    assert db.get_file_hashes(proyecto, "equipoA") == {"a.py": "hash-a.py"}
    assert db.get_file_hashes(proyecto, "equipoB") == {}


def test_indexar_en_un_equipo_no_borra_el_estado_del_otro(proyecto):
    db.log_indexed_files(proyecto, _archivos("a.py"), device_id="equipoA")
    db.log_indexed_files(proyecto, _archivos("b.py"), device_id="equipoB")

    # Antes el DELETE era por project_id a secas y cada equipo pisaba al otro.
    assert db.get_file_hashes(proyecto, "equipoA") == {"a.py": "hash-a.py"}
    assert db.get_file_hashes(proyecto, "equipoB") == {"b.py": "hash-b.py"}


def test_filas_legacy_no_cuentan_como_indice_de_nadie(proyecto):
    """Filas previas a la migracion: device_id NULL, dueño desconocido."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO indexed_files (project_id, file_path, file_size, content_hash) "
            "VALUES (%s, %s, %s, %s)",
            (proyecto, "viejo.py", 10, "hash-viejo"),
        )

    # Ignoradas como hashes -> el primer incremental de cada equipo hace un full
    # y reconstruye su Chroma local (auto-reparacion).
    assert db.get_file_hashes(proyecto, "equipoA") == {}
    # Pero visibles para las lecturas estructurales del audit, que solo miran
    # que archivos tiene el repo.
    assert "viejo.py" in db.get_files_by_path_patterns(proyecto, ["%viejo%"], "equipoA")

    # Y se consumen en el primer reindex real.
    db.log_indexed_files(proyecto, _archivos("a.py"), device_id="equipoA")
    assert db.get_files_by_path_patterns(proyecto, ["%viejo%"], "equipoA") == []


def test_last_indexed_se_registra_por_dispositivo(proyecto):
    db.update_last_indexed(proyecto, "equipoA")

    fila = next(p for p in db.get_all_projects() if p["id"] == proyecto)
    assert "equipoA" in fila["device_indexed_at"]
    assert "equipoB" not in fila["device_indexed_at"]
