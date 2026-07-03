"""Tests del pulido de auditoría e indexado:
- dedupe de hallazgos entre categorías en _consolidate
- presupuesto de caracteres del modo raw
- .gitignore respetado al indexar (repo raíz y repos hijos)
- drift git detectado en repos hijos de un directorio padre
"""

from __future__ import annotations

import git

import tools.audit_project as audit_project
from tools.audit_project import _consolidate, _raw_fragments_within, _verify_summary
import git_client
import indexer


# ---------- _consolidate ----------

def test_consolidate_dedupea_misma_ubicacion_entre_categorias():
    report = {
        "security": {
            "findings": "**[ALTO]** `app/views.py:36` — el try/except silencia el fallo del blacklist de tokens. Fix: propagar la excepción."
        },
        "error_handling": {
            "findings": "**[MEDIO]** `app/views.py:36` — except genérico traga errores al blacklistear. Fix: no tragar."
        },
        "correctness": {
            "findings": "**[CRÍTICO]** `app/models.py:10` — off-by-one en la paginación del listado. Fix: usar el índice correcto."
        },
    }
    summary = _consolidate(report)
    assert summary["total"] == 2
    # sobrevive la copia de mayor severidad (ALTO, no MEDIO)
    sevs = [f["severity"] for f in summary["top"]]
    assert sevs == ["CRÍTICO", "ALTO"]


def test_consolidate_dedupea_redaccion_casi_identica_sin_linea():
    detail = (
        "las API keys ANTHROPIC_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY se cargan "
        "con default vacío en settings sin validación explícita. Fix: validar al arranque."
    )
    report = {
        "security": {"findings": f"**[MEDIO]** {detail}"},
        "config_secrets": {"findings": f"**[ALTO]** {detail} y lanzar error temprano."},
    }
    summary = _consolidate(report)
    assert summary["total"] == 1
    assert summary["top"][0]["severity"] == "ALTO"


def test_consolidate_conserva_hallazgos_distintos():
    report = {
        "security": {
            "findings": (
                "**[ALTO]** `auth/views.py:12` — endpoint sin permiso explícito. Fix: agregar IsAuthenticated.\n"
                "**[BAJO]** `auth/serializers.py:48` — password viaja en texto claro sin HTTPS. Fix: forzar TLS."
            )
        },
    }
    summary = _consolidate(report)
    assert summary["total"] == 2


# ---------- _raw_fragments_within ----------

def _chunk(path: str, lineas: int) -> dict:
    return {
        "file_path": path,
        "chunk_index": 0,
        "start_line": 1,
        "end_line": lineas,
        "content": "x = 1\n" * lineas,
    }


def test_raw_budget_corta_en_limite_de_chunk_y_avisa():
    import deepseek_client

    chunks = [_chunk("primero.py", 50), _chunk("segundo.py", 50), _chunk("tercero.py", 50)]
    presupuesto = len(deepseek_client._build_fragments([chunks[0]])) + 10
    text, used = _raw_fragments_within(chunks, budget=presupuesto, category="security")
    assert "primero.py" in text
    assert "segundo.py" not in text and "tercero.py" not in text
    assert "2 de 3 chunks omitidos" in text
    assert "categories=['security']" in text
    assert used <= presupuesto


def test_raw_budget_amplio_devuelve_todo_sin_aviso():
    chunks = [_chunk("a.py", 10), _chunk("b.py", 10)]
    text, _ = _raw_fragments_within(chunks, budget=float("inf"), category="security")
    assert "a.py" in text and "b.py" in text
    assert "omitidos" not in text


def test_raw_budget_incluye_al_menos_un_chunk():
    chunks = [_chunk("a.py", 100), _chunk("b.py", 100)]
    text, _ = _raw_fragments_within(chunks, budget=1, category="tests")
    assert "a.py" in text  # nunca devuelve vacío si hay presupuesto > 0


# ---------- _verify_summary ----------

def _summary_de_prueba() -> dict:
    return {
        "total": 3,
        "by_severity": {"CRÍTICO": 1, "ALTO": 1, "MEDIO": 1},
        "top": [
            {"severity": "CRÍTICO", "category": "imports",
             "detail": "`app/services.py:97` — falta el import de date. Fix: importarlo."},
            {"severity": "ALTO", "category": "security",
             "detail": "`app/views.py:36` — except silencia el blacklist. Fix: propagar."},
            {"severity": "MEDIO", "category": "correctness",
             "detail": "`app/models.py:10` — off-by-one en paginación. Fix: ajustar índice."},
        ],
    }


def _patch_contexto(monkeypatch):
    monkeypatch.setattr(
        audit_project.db, "get_files_by_path_patterns", lambda pid, pats: ["app/x.py"]
    )
    monkeypatch.setattr(
        audit_project.retriever,
        "get_file_chunks",
        lambda pid, fp: [{"content": "x = 1\n", "chunk_index": 0, "start_line": 1,
                          "end_line": 1, "symbols": ""}],
    )


def test_verify_descarta_y_rebaja(monkeypatch):
    summary = _summary_de_prueba()
    _patch_contexto(monkeypatch)
    respuesta = (
        "1: DESCARTADO — el import existe en la línea 4\n"
        "2: REBAJADO A MEDIO — no hay pérdida de datos demostrable"
    )
    monkeypatch.setattr(
        audit_project.deepseek_client, "_call",
        lambda prompt, chunks: (respuesta, 10, 5, 0.0001),
    )

    _verify_summary(1, summary)

    assert summary["total"] == 2
    assert summary["verificados"] == 2
    assert [f["severity"] for f in summary["top"]] == ["MEDIO", "MEDIO"]
    assert summary["by_severity"] == {"MEDIO": 2}
    assert summary["descartados"][0]["motivo_descarte"].startswith("el import existe")


def test_verify_fail_open_con_respuesta_no_parseable(monkeypatch):
    summary = _summary_de_prueba()
    _patch_contexto(monkeypatch)
    monkeypatch.setattr(
        audit_project.deepseek_client, "_call",
        lambda prompt, chunks: ("[DeepSeek no disponible — fragmentos crudos]", 0, 0, 0.0),
    )

    _verify_summary(1, summary)

    # nada se descarta ni se rebaja: los hallazgos quedan como confirmados
    assert summary["total"] == 3
    assert [f["severity"] for f in summary["top"]] == ["CRÍTICO", "ALTO", "MEDIO"]
    assert "descartados" not in summary


def test_verify_sin_criticos_ni_altos_no_llama(monkeypatch):
    summary = {"total": 1, "by_severity": {"BAJO": 1},
               "top": [{"severity": "BAJO", "category": "tests", "detail": "`a.py:1` — test vacío."}]}

    def _boom(*args, **kwargs):
        raise AssertionError("no debería llamar a DeepSeek")

    monkeypatch.setattr(audit_project.deepseek_client, "_call", _boom)
    assert _verify_summary(1, summary) == (0, 0, 0.0)


# ---------- _git_ignored_paths ----------

def _init_repo(path, gitignore: str) -> git.Repo:
    repo = git.Repo.init(path)
    (path / ".gitignore").write_text(gitignore)
    return repo


def test_gitignore_respetado_en_repo_raiz(tmp_path):
    _init_repo(tmp_path, "generated/\n*.log\n")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "a.py").write_text("x = 1\n")
    (tmp_path / "app.log").write_text("log\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("y = 2\n")

    ignored = indexer._git_ignored_paths(str(tmp_path))
    assert "generated" in ignored
    assert "app.log" in ignored
    assert "src/ok.py" not in ignored and "src" not in ignored


def test_gitignore_respetado_en_repos_hijos(tmp_path):
    child = tmp_path / "Backend"
    child.mkdir()
    _init_repo(child, "ephemeral/\n")
    (child / "ephemeral").mkdir()
    (child / "ephemeral" / "gen.py").write_text("x = 1\n")

    ignored = indexer._git_ignored_paths(str(tmp_path))
    assert "Backend/ephemeral" in ignored


def test_sin_repo_git_no_ignora_nada(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    assert indexer._git_ignored_paths(str(tmp_path)) == set()


# ---------- check_remote_status con repos hijos ----------

def test_check_remote_status_reporta_repos_hijos(tmp_path):
    child = tmp_path / "Backend"
    child.mkdir()
    repo = git.Repo.init(child)
    (child / "a.py").write_text("x = 1\n")
    repo.index.add(["a.py"])  # staged sin commit → dirty

    status = git_client.check_remote_status(str(tmp_path))
    assert status["is_git"] is False
    assert "Backend" in status["child_repos"]
    assert status["child_repos"]["Backend"]["is_git"] is True
    assert status["child_repos"]["Backend"]["dirty"] is True


def test_check_remote_status_sin_hijos(tmp_path):
    status = git_client.check_remote_status(str(tmp_path))
    assert status == {"is_git": False}
