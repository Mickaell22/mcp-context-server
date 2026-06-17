"""Capa 2 del harness — recall END-TO-END del audit (usa DeepSeek + Postgres).

A diferencia del test de retrieval (Capa 1, determinista y gratis), esto corre el
audit COMPLETO contra las fixtures con bugs sembrados y mide cuántos REPORTA de
verdad el modelo. Es no-determinista y cuesta unos centavos: corrélo a mano cuando
afines prompts, categorías, AUDIT_TOP_K o el modelo de embeddings — NO en CI.

Uso (en la máquina del server, con .env configurado):
    cd server && ./.venv/bin/python eval/run_eval.py

Qué hace:
1. Registra+indexa fake_backend y fake_frontend como proyectos efímeros.
2. Corre audit_project(backend, paired_with=frontend) y audit_project(frontend).
3. Para cada bug de expected.json marca HIT si su archivo + alguna keyword
   aparecen en los hallazgos. Imprime scorecard y recall por categoría.
4. Verifica los `must_not_flag`: código que NO debe aparecer en los hallazgos
   de su categoría (termómetro de falsos positivos; clave para over-engineering).
5. Borra los proyectos efímeros (DB + Chroma + whitelist).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

import db  # noqa: E402
from tools import register_project, audit_project, delete_project  # noqa: E402

FIXTURES = SERVER_DIR / "tests" / "fixtures"
BE_NAME = "_eval_fake_backend"
FE_NAME = "_eval_fake_frontend"


def _collect_text(audit_result: dict, only: str | None = None) -> str:
    """Concatena los findings del audit. Si only se pasa, solo esa categoría."""
    report = audit_result.get("audit", {})
    if only:
        return (report.get(only, {}) or {}).get("findings", "") or ""
    return "\n".join((v or {}).get("findings", "") or "" for v in report.values())


def _is_hit(text: str, files, keywords) -> bool:
    low = text.lower()
    files = files if isinstance(files, list) else [files]
    file_ok = any(Path(f).name.lower() in low or f.lower() in low for f in files)
    kw_ok = any(k.lower() in low for k in keywords)
    return file_ok and kw_ok


async def _cleanup():
    for name in (BE_NAME, FE_NAME):
        if db.get_project_by_name(name):
            await delete_project.handle({"project": name}, None)


async def main() -> int:
    expected = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))

    await _cleanup()  # por si quedó algo de una corrida previa

    print("Registrando e indexando fixtures...")
    await register_project.handle({"path": str(FIXTURES / "fake_backend"), "name": BE_NAME}, None)
    await register_project.handle({"path": str(FIXTURES / "fake_frontend"), "name": FE_NAME}, None)

    print("Corriendo audit backend (paired_with frontend)...")
    be_audit = await audit_project.handle({"project": BE_NAME, "paired_with": FE_NAME}, None)
    print("Corriendo audit frontend...")
    fe_audit = await audit_project.handle({"project": FE_NAME}, None)

    be_text = _collect_text(be_audit)
    fe_text = _collect_text(fe_audit)
    contract_text = _collect_text(be_audit, only="contracts")

    rows = []
    for bug in expected["backend"]:
        rows.append((bug, _is_hit(be_text, bug["file"], bug["keywords"])))
    for bug in expected["frontend"]:
        rows.append((bug, _is_hit(fe_text, bug["file"], bug["keywords"])))
    for bug in expected["contracts"]:
        rows.append((bug, _is_hit(contract_text, bug["files"], bug["keywords"])))

    print("\n" + "=" * 72)
    print(f"{'CATEGORÍA':<14}{'SEV':<9}{'ID':<22}{'RESULTADO'}")
    print("-" * 72)
    hits = 0
    for bug, ok in rows:
        hits += ok
        mark = "✓ ENCONTRADO" if ok else "✗ PERDIDO"
        print(f"{bug['category']:<14}{bug['severity']:<9}{bug['id']:<22}{mark}")
    total = len(rows)
    print("-" * 72)
    print(f"RECALL: {hits}/{total} = {hits / total:.0%}")
    cost = (be_audit.get("total_cost_usd", 0) or 0) + (fe_audit.get("total_cost_usd", 0) or 0)
    print(f"Costo DeepSeek aprox: ${cost:.4f}")
    print("=" * 72)

    # Falsos positivos: código que NO debe aparecer en los hallazgos de su categoría.
    # Para over-engineering importa tanto como el recall (no debe sugerir borrar
    # locks ni validación de input externo).
    audits = {"backend": be_audit, "frontend": fe_audit}
    violations = 0
    for item in expected.get("must_not_flag", []):
        audit = audits.get(item.get("project", "backend"), be_audit)
        cat_text = _collect_text(audit, only=item["category"]).lower()
        hit_kw = next((k for k in item["keywords"] if k.lower() in cat_text), None)
        bad = hit_kw is not None
        violations += bad
        mark = f"✗ MARCADO ({hit_kw})" if bad else "✓ respetado"
        print(f"{'no-flag':<14}{'--':<9}{item['id']:<22}{mark}")
    if expected.get("must_not_flag"):
        print(f"FALSOS POSITIVOS: {violations}/{len(expected['must_not_flag'])}")
        print("=" * 72)

    await _cleanup()
    print("Fixtures efímeras eliminadas.")
    return 0 if (hits == total and violations == 0) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"Error en eval: {e}", file=sys.stderr)
        # intento de limpieza best-effort
        try:
            asyncio.run(_cleanup())
        except Exception:
            pass
        sys.exit(2)
