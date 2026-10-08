"""Avance de los indexados (register/index/clone). Es de solo lectura y no toca
disco ni red, asi que responde al instante aunque haya un indexado corriendo."""

import progress


async def handle(args: dict, session_id: int | None) -> dict:
    name = (args.get("project") or "").strip() or None
    jobs = progress.snapshot(name)
    if name and not jobs:
        return {"error": f"No hay indexado registrado para '{name}' en este proceso del server"}
    return {"jobs": jobs}
