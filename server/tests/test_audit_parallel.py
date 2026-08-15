"""Paralelismo del audit: los lotes de todas las categorias se reparten en una
sola pool y se re-agrupan por categoria sin perder el orden.

Offline: reemplaza deepseek_client._call por un fake, no toca red ni Postgres.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import deepseek_client


# El marcador va en file_path; el content es relleno de 100 chars para que con
# AUDIT_BATCH_MAX_CHARS=40 cada chunk caiga en su propio lote.
def _chunk(marker: str) -> dict:
    return {"file_path": marker, "chunk_index": 0, "content": "x" * 100, "start_line": 1, "end_line": 1}


def _fake_call(monkeypatch, tracker=None):
    """_call falso: devuelve el marcador del lote y 10/5 tokens."""
    def fake(prompt: str, chunks: list[dict]):
        if tracker is not None:
            tracker.append(threading.current_thread().name)
        return f"**[ALTO]** `{chunks[0]['file_path']}` — problema.", 10, 5, 0.001

    monkeypatch.setattr(deepseek_client, "_call", fake)


def test_agrupa_por_key_y_respeta_orden_de_lotes(monkeypatch):
    _fake_call(monkeypatch)
    # 40 chars por lote fuerza un lote por chunk
    monkeypatch.setattr(deepseek_client, "AUDIT_BATCH_MAX_CHARS", 40)

    jobs = [
        ("security", "instr", [_chunk("s0"), _chunk("s1"), _chunk("s2")]),
        ("tests", "instr", [_chunk("t0")]),
    ]
    out = deepseek_client.audit_batches(jobs, max_workers=4)

    assert set(out) == {"security", "tests"}
    # los 3 lotes de security vuelven concatenados EN ORDEN pese a correr en paralelo
    assert out["security"][0].splitlines() == [
        "**[ALTO]** `s0` — problema.",
        "**[ALTO]** `s1` — problema.",
        "**[ALTO]** `s2` — problema.",
    ]
    assert out["tests"][0] == "**[ALTO]** `t0` — problema."


def test_suma_tokens_y_costo_de_todos_los_lotes(monkeypatch):
    _fake_call(monkeypatch)
    monkeypatch.setattr(deepseek_client, "AUDIT_BATCH_MAX_CHARS", 40)

    out = deepseek_client.audit_batches(
        [("security", "instr", [_chunk("s0"), _chunk("s1"), _chunk("s2")])]
    )
    _, in_tok, out_tok, cost = out["security"]
    assert (in_tok, out_tok) == (30, 15)  # 3 lotes x (10, 5)
    assert abs(cost - 0.003) < 1e-9


def test_usa_varios_hilos(monkeypatch):
    threads: list[str] = []
    _fake_call(monkeypatch, threads)
    monkeypatch.setattr(deepseek_client, "AUDIT_BATCH_MAX_CHARS", 40)

    deepseek_client.audit_batches(
        [("a", "i", [_chunk(f"c{i}") for i in range(8)])], max_workers=4
    )
    assert len(set(threads)) > 1, "los lotes corrieron todos en el mismo hilo"


def test_job_sin_chunks_no_llama_al_modelo(monkeypatch):
    def boom(prompt, chunks):
        raise AssertionError("no debe llamarse sin chunks")

    monkeypatch.setattr(deepseek_client, "_call", boom)
    out = deepseek_client.audit_batches([("vacia", "instr", [])])
    assert out["vacia"] == ("Sin hallazgos.", 0, 0, 0.0)


def test_audit_context_mantiene_la_tupla_de_siempre(monkeypatch):
    _fake_call(monkeypatch)
    findings, in_tok, out_tok, cost = deepseek_client.audit_context("instr", [_chunk("x")])
    assert findings == "**[ALTO]** `x` — problema."
    assert (in_tok, out_tok) == (10, 5)
    assert cost == 0.001


# ---------- tarifas por franja horaria (DeepSeek cobra el doble en peak) ----------

def _at(hour: int) -> datetime:
    return datetime(2026, 8, 20, hour, 30, tzinfo=timezone.utc)


def test_horas_peak_y_offpeak_segun_utc():
    # peak declarado: 01-04 y 06-10 UTC
    assert deepseek_client._is_peak(_at(2)) is True
    assert deepseek_client._is_peak(_at(7)) is True
    assert deepseek_client._is_peak(_at(0)) is False
    assert deepseek_client._is_peak(_at(4)) is False, "el fin del rango es exclusivo"
    assert deepseek_client._is_peak(_at(10)) is False
    # 17:00 UTC = mediodia en Ecuador: la franja barata
    assert deepseek_client._is_peak(_at(17)) is False


def test_la_tarifa_peak_es_el_doble(monkeypatch):
    monkeypatch.setattr(deepseek_client, "_is_peak", lambda: False)
    off_in, off_out = deepseek_client._rates()
    monkeypatch.setattr(deepseek_client, "_is_peak", lambda: True)
    peak_in, peak_out = deepseek_client._rates()
    assert abs(peak_in - off_in * 2) < 1e-12
    assert abs(peak_out - off_out * 2) < 1e-12


def test_rango_horario_mal_escrito_no_tumba_el_server(monkeypatch):
    monkeypatch.setattr(deepseek_client, "DEEPSEEK_PEAK_HOURS_UTC", "1-4,basura,6-10")
    assert deepseek_client._peak_ranges() == [(1, 4), (6, 10)]


def test_sin_hallazgos_no_ensucia_la_concatenacion(monkeypatch):
    def fake(prompt: str, chunks: list[dict]):
        # el primer lote no encuentra nada, el segundo si
        if chunks[0]["file_path"] == "c0":
            return "Sin hallazgos.", 1, 1, 0.0
        return "**[BAJO]** `c1` — nimiedad.", 1, 1, 0.0

    monkeypatch.setattr(deepseek_client, "_call", fake)
    monkeypatch.setattr(deepseek_client, "AUDIT_BATCH_MAX_CHARS", 40)

    out = deepseek_client.audit_batches([("a", "i", [_chunk("c0"), _chunk("c1")])])
    assert out["a"][0] == "**[BAJO]** `c1` — nimiedad."
