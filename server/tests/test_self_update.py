"""Auto-actualizacion del propio server MCP.

Offline: monta un repo "remoto" local y un clon retrasado, sin salir a GitHub.
Ejercita el camino completo detectar -> actualizar, y sus guardarrailes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

git = pytest.importorskip("git")

import self_update


def _commit(repo, path: Path, nombre: str, contenido: str, mensaje: str):
    (path / nombre).write_text(contenido)
    repo.index.add([nombre])
    return repo.index.commit(mensaje)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """(clon_retrasado, remoto). El clon esta 2 commits detras del remoto."""
    origen = tmp_path / "origen"
    origen.mkdir()
    r = git.Repo.init(origen)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@test")
    _commit(r, origen, "main.py", "v1\n", "version inicial")
    base = r.head.commit.hexsha

    clon_path = tmp_path / "clon"
    git.Repo.clone_from(str(origen), str(clon_path))

    # dos commits nuevos SOLO en el remoto: uno normal y otro que toca las deps
    _commit(r, origen, "main.py", "v2\n", "feat: tool nueva")
    (origen / "server").mkdir(exist_ok=True)
    _commit(r, origen, "server/requirements.txt", "anthropic\nnueva-dep\n", "chore: dependencia nueva")

    clon = git.Repo(clon_path)
    with clon.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@test")

    monkeypatch.setattr(self_update, "REPO_ROOT", clon_path)
    monkeypatch.setattr(self_update, "REQUIREMENTS", clon_path / "server" / "requirements.txt")
    self_update._cache = None
    return clon_path, origen, base


def test_detecta_los_commits_nuevos_del_remoto(repos):
    estado = self_update.check(force=True)
    assert estado["update_available"] is True
    assert estado["behind"] == 2
    subjects = [c["subject"] for c in estado["commits"]]
    assert "feat: tool nueva" in subjects, f"no listo que trae la version nueva: {subjects}"


def test_version_local_reporta_el_commit_que_corre(repos):
    v = self_update.local_version()
    assert v["subject"] == "version inicial"
    assert v["dirty"] is False


def test_el_cache_evita_un_fetch_por_llamada(repos, monkeypatch):
    self_update.check(force=True)
    # si volviera a tocar el repo, esto lo delataria
    monkeypatch.setattr(self_update, "_repo", lambda: pytest.fail("uso el repo pese al cache"))
    assert self_update.check()["update_available"] is True


def test_actualizar_deja_el_clon_al_dia_y_reinstala_deps(repos, monkeypatch):
    clon_path, _, _ = repos
    llamadas = []
    monkeypatch.setattr(self_update, "_install_deps", lambda: llamadas.append(1) or {"ok": True})

    out = self_update.apply_update()
    assert out["ok"] is True and out["updated"] is True
    assert (clon_path / "main.py").read_text() == "v2\n"
    assert llamadas, "requirements.txt cambio y no reinstalo las dependencias"
    assert "REINICIA" in out["next_step"]
    # y ya no hay nada pendiente
    assert self_update.check(force=True)["update_available"] is False


def test_no_reinstala_deps_si_requirements_no_cambio(repos, monkeypatch, tmp_path):
    clon_path, origen, base = repos
    # se retrocede el remoto al commit que NO toca requirements.txt
    git.Repo(origen).git.reset("--hard", "HEAD~1")
    monkeypatch.setattr(self_update, "_install_deps", lambda: pytest.fail("reinstalo sin necesidad"))

    out = self_update.apply_update()
    assert out["ok"] is True
    assert out["deps"]["skipped"]


def test_no_pisa_cambios_sin_commitear(repos):
    clon_path, _, _ = repos
    (clon_path / "main.py").write_text("trabajo en curso\n")

    out = self_update.apply_update()
    assert out["ok"] is False and out["updated"] is False
    assert "sin commitear" in out["error"]
    assert (clon_path / "main.py").read_text() == "trabajo en curso\n", "piso el trabajo local"


def test_sin_novedades_no_hace_nada(repos, monkeypatch):
    monkeypatch.setattr(self_update, "_install_deps", lambda: {"ok": True})
    self_update.apply_update()  # deja el clon al dia

    # ya al dia: la segunda no debe tocar nada
    monkeypatch.setattr(self_update, "_install_deps", lambda: pytest.fail("no habia que actualizar"))
    out = self_update.apply_update()
    assert out["ok"] is True and out["updated"] is False
    assert "ultima version" in out["message"]


def test_un_repo_sin_remoto_no_revienta(tmp_path, monkeypatch):
    solo = tmp_path / "solo"
    solo.mkdir()
    r = git.Repo.init(solo)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "t")
        cw.set_value("user", "email", "t@t")
    _commit(r, solo, "a.py", "x\n", "inicial")

    monkeypatch.setattr(self_update, "REPO_ROOT", solo)
    self_update._cache = None
    estado = self_update.check(force=True)
    assert estado["update_available"] is False
    assert "remoto" in estado["note"]
