"""Tool MCP: version del propio servidor y actualizacion desde GitHub."""

from __future__ import annotations

import asyncio

import self_update


async def handle(args: dict, session_id: int | None) -> dict:
    if bool(args.get("update", False)):
        # git pull + pip pueden tardar: fuera del event loop
        return await asyncio.to_thread(self_update.apply_update)

    # force=True: si lo pedis explicitamente es porque queres el dato de ahora,
    # no el del cache que alimenta el aviso pasivo de list_projects.
    return await asyncio.to_thread(self_update.check, True)
