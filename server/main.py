import json
import logging
import sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import db
import security
from config import LOG_LEVEL
from tools import query_context, index_project, list_projects, clone_project

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

app = Server("mcp-context-server")

# session_id se crea por proyecto consultado; None si no hay proyecto activo
_active_sessions: dict[str, int] = {}


def _get_or_create_session(project_name: str) -> int | None:
    if project_name not in _active_sessions:
        project = db.get_project_by_name(project_name)
        if not project:
            return None
        _active_sessions[project_name] = db.create_session(project["id"])
    return _active_sessions[project_name]


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="query_context",
            description="Consulta contexto relevante de un proyecto dado una pregunta en lenguaje natural.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Pregunta o descripcion de lo que se busca"},
                    "project": {"type": "string", "description": "Nombre del proyecto a consultar"},
                },
                "required": ["query", "project"],
            },
        ),
        types.Tool(
            name="index_project",
            description="Re-indexa un proyecto existente en disco. Usar cuando hay cambios en el codigo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto a indexar"},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="list_projects",
            description="Lista los proyectos disponibles para consultar.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="clone_project",
            description="Clona un repo de GitHub e indexa automaticamente. Soporta repos publicos y privados.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_url": {"type": "string", "description": "URL del repositorio GitHub a clonar"},
                },
                "required": ["repo_url"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    session_id = None

    if name == "query_context":
        project_name = arguments.get("project", "")
        session_id = _get_or_create_session(project_name)
        result = await query_context.handle(arguments, session_id)

    elif name == "index_project":
        result = await index_project.handle(arguments, session_id)

    elif name == "list_projects":
        result = await list_projects.handle(arguments, session_id)

    elif name == "clone_project":
        result = await clone_project.handle(arguments, session_id)

    else:
        result = {"error": f"Tool desconocido: {name}"}

    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    logger.info("Iniciando MCP Context Server...")

    paths = db.load_project_paths()
    security.load_allowed_paths(paths)
    logger.info("Whitelist cargada: %d proyectos", len(paths))

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
