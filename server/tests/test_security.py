"""Tests de seguridad: validate_project_path e is_file_allowed.

No tocan DB ni red. La whitelist (`_allowed_paths`) se manipula con las
funciones publicas de `security` y se restaura en un fixture por test.
"""

import os

import pytest

import security


@pytest.fixture(autouse=True)
def reset_whitelist():
    """Aisla la whitelist global entre tests."""
    saved = set(security._allowed_paths)
    security._allowed_paths.clear()
    yield
    security._allowed_paths.clear()
    security._allowed_paths.update(saved)


# ---------- validate_project_path ----------

def test_validate_project_path_dentro_de_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    proj = base / "miapp"
    proj.mkdir(parents=True)
    monkeypatch.setattr(security, "PROJECTS_BASE_PATH", str(base))

    valid, reason = security.validate_project_path(str(proj))
    assert valid is True
    assert reason == ""


def test_validate_project_path_es_la_base_misma(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(security, "PROJECTS_BASE_PATH", str(base))

    valid, reason = security.validate_project_path(str(base))
    assert valid is True


def test_validate_project_path_fuera_de_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    fuera = tmp_path / "otro"
    fuera.mkdir()
    monkeypatch.setattr(security, "PROJECTS_BASE_PATH", str(base))

    valid, reason = security.validate_project_path(str(fuera))
    assert valid is False
    assert "fuera de PROJECTS_BASE_PATH" in reason


def test_validate_project_path_no_existe(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(security, "PROJECTS_BASE_PATH", str(base))

    valid, reason = security.validate_project_path(str(base / "no-existe"))
    assert valid is False
    assert "no existe" in reason


def test_validate_project_path_no_confunde_prefijo(tmp_path, monkeypatch):
    """'/base-evil' no debe colar como hijo de '/base'."""
    base = tmp_path / "base"
    base.mkdir()
    evil = tmp_path / "base-evil"
    evil.mkdir()
    monkeypatch.setattr(security, "PROJECTS_BASE_PATH", str(base))

    valid, _ = security.validate_project_path(str(evil))
    assert valid is False


# ---------- is_file_allowed ----------

def test_is_file_allowed_archivo_permitido(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))
    f = proj / "main.py"

    allowed, reason = security.is_file_allowed(str(f))
    assert allowed is True
    assert reason == ""


def test_is_file_allowed_extension_bloqueada(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))

    allowed, reason = security.is_file_allowed(str(proj / "id_rsa.pem"))
    assert allowed is False
    assert "extension bloqueada" in reason


def test_is_file_allowed_nombre_bloqueado(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))

    allowed, reason = security.is_file_allowed(str(proj / ".env"))
    assert allowed is False
    assert "archivo bloqueado" in reason


def test_is_file_allowed_claude_md_bloqueado(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))

    allowed, reason = security.is_file_allowed(str(proj / "CLAUDE.md"))
    assert allowed is False
    assert "archivo bloqueado" in reason


def test_is_file_allowed_extension_no_permitida(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))

    allowed, reason = security.is_file_allowed(str(proj / "imagen.png"))
    assert allowed is False
    assert "no permitida" in reason


def test_is_file_allowed_fuera_de_whitelist(tmp_path):
    # whitelist vacia (fixture la limpio): cualquier ruta valida-por-extension
    # debe rechazarse por no estar permitida.
    f = tmp_path / "proj" / "main.py"

    allowed, reason = security.is_file_allowed(str(f))
    assert allowed is False
    assert "fuera de la whitelist" in reason


def test_is_path_allowed_incluye_subdirectorios(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))

    assert security.is_path_allowed(str(proj / "src" / "deep" / "file.py")) is True
    assert security.is_path_allowed(str(tmp_path / "otro")) is False


def test_remove_allowed_path(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    security.add_allowed_path(str(proj))
    assert security.is_path_allowed(str(proj)) is True

    security.remove_allowed_path(str(proj))
    assert security.is_path_allowed(str(proj)) is False
