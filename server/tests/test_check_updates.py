"""check_updates: frescura del indice y guardarrail del sync.

Offline: crea repos git temporales de verdad (sin remoto) y monkeypatchea el
fetch. No toca Postgres ni la red.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

git = pytest.importorskip("git")

import git_client
from tools import check_updates


@pytest.fixture
def repo(tmp_path):
    """Repo git con un commit. Devuelve (path, fecha_del_commit)."""
    path = tmp_path / "proyecto"
    path.mkdir()
    r = git.Repo.init(path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@test")
    (path / "a.py").write_text("print(1)\n")
    r.index.add(["a.py"])
    r.index.commit("inicial")
    return str(path), r.head.commit.committed_datetime


# ---------- local_state / index_stale ----------

def test_local_state_lee_commit_y_limpieza(repo):
    path, commit_dt = repo
    st = git_client.local_state(path)
    assert st["is_git"] is True
    assert st["dirty"] is False
    assert st["last_commit"] == commit_dt.isoformat()


def test_local_state_detecta_dirty(repo):
    path, _ = repo
    (Path(path) / "a.py").write_text("print(2)\n")
    assert git_client.local_state(path)["dirty"] is True


def test_indice_viejo_si_el_commit_es_posterior(repo):
    path, commit_dt = repo
    local = git_client.local_state(path)
    antes = (commit_dt - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    despues = (commit_dt + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    # el naive se interpreta como hora local; el commit trae offset propio.
    # Se comparan instantes, no strings.
    assert check_updates.is_index_stale(local, antes) is True
    assert check_updates.is_index_stale(local, despues) is False


def test_nunca_indexado_aqui_cuenta_como_viejo(repo):
    path, _ = repo
    assert check_updates.is_index_stale(git_client.local_state(path), None) is True


def test_indexado_ilegible_no_revienta(repo):
    path, _ = repo
    assert check_updates.is_index_stale(git_client.local_state(path), "no-es-fecha") is True


# ---------- guardarrail del sync ----------

def _state(**kw) -> dict:
    base = {"behind": 0, "ahead": 0, "dirty": False, "index_stale": False, "is_git": True}
    base.update(kw)
    return base


def _sync(monkeypatch, st, tmp_path):
    """Corre _sync_one con pull y reindex falsos; devuelve (resultado, llamadas)."""
    calls: list[str] = []

    def fake_pull(path):
        calls.append("pull")
        return {"ok": True, "output": "Updating"}

    async def fake_index(args, session_id):
        calls.append("reindex")
        return {"files_indexed": 1}

    monkeypatch.setattr(git_client, "pull_repo", fake_pull)
    monkeypatch.setattr(check_updates.index_project, "handle", fake_index)
    result = asyncio.run(check_updates._sync_one("p", str(tmp_path), st))
    return result, calls


def test_sync_hace_pull_y_reindexa_si_esta_limpio(monkeypatch, tmp_path):
    result, calls = _sync(monkeypatch, _state(behind=3), tmp_path)
    assert calls == ["pull", "reindex"]
    assert result["pull"]["ok"] is True


def test_sync_no_toca_un_repo_dirty(monkeypatch, tmp_path):
    result, calls = _sync(monkeypatch, _state(behind=3, dirty=True), tmp_path)
    assert "pull" not in calls, "hizo pull sobre cambios sin commitear"
    assert result["pull"]["ok"] is False
    assert "sin commitear" in result["pull"]["skipped"]


def test_sync_no_toca_un_repo_con_commits_sin_pushear(monkeypatch, tmp_path):
    result, calls = _sync(monkeypatch, _state(behind=3, ahead=2), tmp_path)
    assert "pull" not in calls
    assert "sin pushear" in result["pull"]["skipped"]


def test_sync_reindexa_sin_pull_si_solo_el_indice_esta_viejo(monkeypatch, tmp_path):
    _, calls = _sync(monkeypatch, _state(index_stale=True), tmp_path)
    assert calls == ["reindex"]


def test_sync_reindexa_un_repo_dirty_sin_hacer_pull(monkeypatch, tmp_path):
    # el reindexado es solo lectura: refleja el trabajo en curso sin riesgo
    _, calls = _sync(monkeypatch, _state(dirty=True, index_stale=True), tmp_path)
    assert calls == ["reindex"]


# ---------- ramas: funciona en cualquiera, no solo main/master ----------

def test_compara_la_rama_activa_sea_cual_sea(tmp_path):
    """Un clon parado en feature/x debe medirse contra origin/feature/x."""
    origen = tmp_path / "origen"
    origen.mkdir()
    r = git.Repo.init(origen)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "t")
        cw.set_value("user", "email", "t@t")
    (origen / "a.py").write_text("v1\n")
    r.index.add(["a.py"])
    r.index.commit("inicial")
    r.git.checkout("-b", "feature/orders")
    (origen / "a.py").write_text("v2\n")
    r.index.add(["a.py"])
    r.index.commit("trabajo en la feature")

    clon = tmp_path / "clon"
    git.Repo.clone_from(str(origen), str(clon), branch="feature/orders")

    # el remoto avanza SOLO en la rama de feature
    (origen / "a.py").write_text("v3\n")
    r.index.add(["a.py"])
    r.index.commit("mas trabajo")

    st = git_client.check_remote_status(str(clon))
    assert st["branch"] == "feature/orders"
    assert st["behind"] == 1, "no detecto el commit nuevo de una rama que no es main"


def test_rama_sin_upstream_no_se_reporta_como_al_dia():
    # rama local recien creada, nunca pusheada: no hay contra que comparar.
    # Reportarla como "al dia" seria mentir.
    st = _state(comparable=False, branch="feature/nueva")
    status, action = check_updates._describe(st)
    assert status != "al dia"
    assert "no sigue a ninguna remota" in status
    assert "git push -u origin feature/nueva" in action


def test_head_detached_se_avisa():
    status, _ = check_updates._describe(_state(detached=True, comparable=False))
    assert "detached" in status


# ---------- descripcion legible ----------

def test_describe_al_dia():
    assert check_updates._describe(_state()) == ("al dia", "nada")


def test_describe_reporta_el_drift_de_los_repos_hijos():
    # proyecto registrado como directorio padre (EcuaInventario/ con Backend/ y
    # Frontend/): en la raiz no hay repo git, el drift vive en los hijos
    st = _state(is_git=False, child_repos={
        "Backend": {"behind": 3, "ahead": 0, "dirty": False},
        "Frontend": {"behind": 0, "ahead": 0, "dirty": True},
        "Docs": {"behind": 0, "ahead": 0, "dirty": False},
    })
    status, action = check_updates._describe(st)
    assert "Backend: 3 detras" in status
    assert "Frontend: sin commitear" in status
    assert "Docs" not in status, "un hijo al dia no deberia aparecer"
    assert "repo hijo" in action


def test_describe_acumula_problemas_y_acciones():
    status, action = check_updates._describe(_state(behind=2, index_stale=True))
    assert "2 commit(s) detras" in status and "indice detras" in status
    assert action == "git pull + index_project(incremental=true)"
