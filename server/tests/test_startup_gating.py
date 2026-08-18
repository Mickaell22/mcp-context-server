"""Regresion del fix de arranque: call_tool no debe avanzar hasta que
_db_ready este seteado.

Antes, ensure_schema/claim_local_paths/load_project_paths (round-trips reales
a la Postgres remota de Railway) corrian ANTES de entrar a stdio_server(), asi
que el handshake MCP no empezaba a leer stdin hasta que volvian esas llamadas
de red. Si Railway tardaba de mas, Claude Code daba el servidor por caido
antes de que respondiera. Ahora esa inicializacion corre en background y
call_tool espera un asyncio.Event en vez de asumir que la whitelist ya esta
cargada.
"""

import asyncio
import main


def test_call_tool_espera_a_db_ready():
    async def _run():
        main._db_ready.clear()
        task = asyncio.create_task(main.call_tool("list_projects", {}))
        try:
            done, _pending = await asyncio.wait({task}, timeout=0.2)
            assert task not in done, "call_tool no deberia avanzar sin _db_ready"

            main._db_ready.set()
            done, _pending = await asyncio.wait({task}, timeout=2)
            assert task in done, "call_tool deberia desbloquearse al setear _db_ready"
        finally:
            if not task.done():
                task.cancel()

    asyncio.run(_run())
