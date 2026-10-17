"""Benchmark de latencia y tokens para comparar ramas (antes/despues).

Uso (desde server/):  .venv/bin/python eval/bench.py salida.json

Llama a los handlers de las tools directamente (sin MCP) con session_id=None,
asi no escribe en la tabla `queries`. Usa DeepSeek y Postgres reales (~centavos).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import indexer  # noqa: E402
import security  # noqa: E402
from config import DEVICE_ID  # noqa: E402
from tools import list_projects, query_context  # noqa: E402

PROJECT = "mcp-context-server"
QUERIES = [
    "como se calcula el costo de una llamada a DeepSeek y la tarifa peak",
    "donde se reordenan los chunks con el score lexico del reranking",
    "como detecta index_project el drift git antes de indexar",
]


def _timed(fn, *a, **kw):
    t = time.perf_counter()
    out = fn(*a, **kw)
    return out, time.perf_counter() - t


async def main(out_path: str) -> None:
    res: dict = {}
    security.load_allowed_paths(db.load_project_paths())

    db_times = [_timed(db.get_project_by_name, PROJECT)[1] for _ in range(5)]
    res["db_call_s"] = round(statistics.median(db_times), 3)

    qc = []
    for q in QUERIES:
        t = time.perf_counter()
        r = await query_context.handle({"query": q, "project": PROJECT, "code_only": True}, None)
        qc.append({
            "query": q,
            "seconds": round(time.perf_counter() - t, 2),
            "tokens_used": r.get("tokens_used"),
            "context_chars": len(r.get("context", "")),
            "context": r.get("context") or r.get("error"),
        })
    res["query_context"] = qc
    res["query_context_avg_s"] = round(statistics.mean(x["seconds"] for x in qc), 2)
    res["query_context_avg_tokens"] = round(statistics.mean(x["tokens_used"] or 0 for x in qc))

    t = time.perf_counter()
    lp = await list_projects.handle({}, None)
    res["list_projects_s"] = round(time.perf_counter() - t, 2)
    res["list_projects_chars"] = len(json.dumps(lp, ensure_ascii=False))

    p = db.get_project_by_name(PROJECT)
    path = db.resolve_project_path(p, DEVICE_ID)
    _, secs = _timed(indexer.index_project, p["id"], path, True)
    res["index_incremental_s"] = round(secs, 2)

    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != "query_context"}, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "bench.json"))
