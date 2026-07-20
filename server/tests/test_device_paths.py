"""Tests de resolucion de ruta por dispositivo (multi-equipo, misma Postgres).

Solo cubre la logica pura `db.resolve_project_path`; no toca Postgres.
"""

import db


def test_usa_ruta_del_dispositivo_actual():
    project = {
        "path": "/home/mickaell/Escritorio/proj",  # legacy (otro equipo)
        "device_paths": {
            "desktop": "/home/mickaell/Desktop/proj",
            "laptop": "/home/user/proj",
        },
    }
    assert db.resolve_project_path(project, "desktop") == "/home/mickaell/Desktop/proj"
    assert db.resolve_project_path(project, "laptop") == "/home/user/proj"


def test_fallback_a_path_legacy_si_falta_el_dispositivo():
    project = {
        "path": "/home/mickaell/legacy/proj",
        "device_paths": {"laptop": "/home/user/proj"},
    }
    # 'desktop' no esta en device_paths -> cae al path legacy.
    assert db.resolve_project_path(project, "desktop") == "/home/mickaell/legacy/proj"


def test_device_paths_vacio_o_ausente():
    assert db.resolve_project_path(
        {"path": "/x", "device_paths": {}}, "desktop"
    ) == "/x"
    # device_paths None (columna recien migrada leida como null) -> fallback.
    assert db.resolve_project_path(
        {"path": "/x", "device_paths": None}, "desktop"
    ) == "/x"
    # sin la clave device_paths del todo.
    assert db.resolve_project_path({"path": "/x"}, "desktop") == "/x"
