from __future__ import annotations

import asyncio
import json
import logging
import sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import db
import security
from config import LOG_LEVEL
import self_update
from tools import query_context, index_project, list_projects, clone_project, get_file, register_project, audit_project, find_usages, delete_project, check_updates, describe_project, check_server_version

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

app = Server("mcp-context-server")

# session_id se crea por proyecto consultado; None si no hay proyecto activo
_active_sessions: dict[str, int] = {}

# Se setea cuando termina la inicializacion de DB (ensure_schema/claim_local_paths/
# load_project_paths), que son round-trips reales a la Postgres remota de Railway.
# call_tool espera este evento en vez de asumir que la whitelist ya esta cargada.
_db_ready = asyncio.Event()


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
            description="Consulta contexto relevante de uno o varios proyectos dado una pregunta en lenguaje natural.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Pregunta o descripcion de lo que se busca"},
                    "project": {
                        "description": "Nombre del proyecto o lista de proyectos para queries cross-repo",
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                    },
                    "code_only": {"type": "boolean", "description": "Si es true, excluye archivos de documentacion (.md, .txt) y busca solo en codigo fuente"},
                    "top_k": {"type": "integer", "description": "Numero de fragmentos a recuperar (opcional). Por defecto 8; subelo para proyectos grandes."},
                },
                "required": ["query", "project"],
            },
        ),
        types.Tool(
            name="index_project",
            description="Re-indexa un proyecto existente en disco. incremental=true solo procesa archivos cambiados. Antes de indexar verifica drift git (local detras del remoto o con cambios sin commitear) y, si lo detecta, devuelve needs_confirmation en vez de indexar: hay que reintentar con acknowledge_drift=true para indexar el estado local de todos modos.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto a indexar"},
                    "incremental": {"type": "boolean", "description": "Si es true, solo re-indexa archivos modificados desde el ultimo index"},
                    "acknowledge_drift": {"type": "boolean", "description": "Si es true, indexa aunque el local este detras del remoto o tenga cambios sin commitear (segunda confirmacion tras un needs_confirmation)"},
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
        types.Tool(
            name="register_project",
            description="Registra e indexa un proyecto local en disco sin necesidad de clonarlo desde GitHub.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta absoluta al directorio del proyecto"},
                    "name": {"type": "string", "description": "Nombre del proyecto (opcional, usa el nombre del directorio si se omite)"},
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="get_file",
            description="Retorna el contenido completo de un archivo especifico del indice.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto"},
                    "file_path": {"type": "string", "description": "Ruta relativa del archivo dentro del proyecto"},
                },
                "required": ["project", "file_path"],
            },
        ),
        types.Tool(
            name="audit_project",
            description="Corre una bateria de queries de auditoria predefinidas sobre un proyecto y genera un reporte consolidado con hallazgos estructurados (severidad/archivo/linea/fix). Detecta automaticamente si es backend o frontend y corre las categorias correspondientes (incluye 'correctness' para cazar bugs de logica). Con paired_with audita ademas el CONTRATO API entre dos repos hermanos (front+back).",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto a auditar"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categorias a auditar. Si se omite, corre todas las del tipo detectado (backend o frontend).",
                    },
                    "paired_with": {"type": "string", "description": "Nombre del proyecto hermano (ej. el frontend si auditas el backend) para auditar el contrato API entre ambos: campos, nullability, tipos y endpoints que no calzan."},
                    "raw": {"type": "boolean", "description": "Si es true, devuelve los chunks crudos numerados (archivo:linea) sin compresion DeepSeek — alta fidelidad para razonar bugs directamente, coste 0 de LLM."},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="delete_project",
            description="Elimina un proyecto del indice: borra sus chunks de ChromaDB, sus filas en PostgreSQL y lo saca de la whitelist. Accion irreversible.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto a eliminar"},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="check_server_version",
            description="Version del PROPIO servidor MCP y actualizacion desde GitHub (distinto de check_updates, que mira los proyectos indexados). Sin argumentos informa que commit corre este equipo y que commits nuevos hay en el remoto. Con update=true hace git pull --ff-only del repo del server y, solo si el commit nuevo toco requirements.txt, reinstala las dependencias en su venv. Nunca se reinicia solo: despues hay que reiniciar Claude Code para cargar el codigo nuevo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "update": {"type": "boolean", "description": "Si es true, actualiza el server (pull + deps si cambiaron). Si es false o se omite, solo informa."},
                },
            },
        ),
        types.Tool(
            name="describe_project",
            description="Reconocimiento de un proyecto ANTES de escribir codigo en el: que patrones sigue y como se conecta. Devuelve datos medidos del indice sin LLM (stack y dependencias, paleta de colores con sus tokens, modulos mas importados, estructura de carpetas, convencion de nombres) MAS una guia sintetizada (arquitectura, unidad tipica, flujo de datos, pasos para agregar una feature siguiendo el patron existente). El resultado se cachea hasta el proximo re-indexado.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto a perfilar"},
                    "refresh": {"type": "boolean", "description": "Si es true, regenera el perfil aunque haya uno cacheado vigente."},
                    "focus": {"type": "string", "description": "Zona concreta en la que se va a trabajar (ej. 'autenticacion', 'carrito'). Sesga la guia hacia esa parte; los perfiles con focus no se cachean."},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="check_updates",
            description="Revisa que proyectos de este equipo estan desactualizados: si el repo local esta detras de GitHub (behind/ahead/dirty, hace fetch) y si el indice de Chroma esta detras del codigo. Con sync=true los pone al dia: git pull (solo si el fast-forward es seguro: nunca con cambios sin commitear ni commits sin pushear) + re-indexado incremental.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre de un proyecto concreto (opcional). Si se omite, revisa todos los de este equipo."},
                    "sync": {"type": "boolean", "description": "Si es true, ademas de reportar hace git pull (cuando es seguro) y re-indexa lo que quedo viejo."},
                },
            },
        ),
        types.Tool(
            name="find_usages",
            description="Busca que archivos importan o usan un simbolo, clase, funcion o modulo especifico. Requiere que el proyecto haya sido indexado con esta version del servidor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Nombre del proyecto"},
                    "symbol": {"type": "string", "description": "Nombre del simbolo a buscar (clase, funcion, modulo)"},
                },
                "required": ["project", "symbol"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    await _db_ready.wait()
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

    elif name == "register_project":
        result = await register_project.handle(arguments, session_id)

    elif name == "get_file":
        result = await get_file.handle(arguments, session_id)

    elif name == "audit_project":
        project_name = arguments.get("project", "")
        session_id = _get_or_create_session(project_name)
        result = await audit_project.handle(arguments, session_id)

    elif name == "delete_project":
        result = await delete_project.handle(arguments, session_id)

    elif name == "describe_project":
        project_name = arguments.get("project", "")
        session_id = _get_or_create_session(project_name)
        result = await describe_project.handle(arguments, session_id)

    elif name == "check_updates":
        result = await check_updates.handle(arguments, session_id)

    elif name == "check_server_version":
        result = await check_server_version.handle(arguments, session_id)

    elif name == "find_usages":
        result = await find_usages.handle(arguments, session_id)

    else:
        result = {"error": f"Tool desconocido: {name}"}

    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    from config import DEVICE_ID

    logger.info("Iniciando MCP Context Server... (device_id=%s)", DEVICE_ID)

    async def _startup_db_init() -> None:
        # Corre en segundo plano, DESPUES del handshake MCP: ensure_schema,
        # claim_local_paths y load_project_paths son round-trips reales a la
        # Postgres remota de Railway, y si corren antes de stdio_server() el
        # proceso no llega a leer stdin hasta que vuelven — eso es lo que hacia
        # que Claude Code diera el servidor por caido si Railway tardaba de mas.
        def _init_sync() -> list[str]:
            db.ensure_schema()
            claimed = db.claim_local_paths(DEVICE_ID)
            if claimed:
                logger.info("Rutas locales adoptadas para %s: %d proyecto(s)", DEVICE_ID, claimed)
            return db.load_project_paths()

        try:
            paths = await asyncio.to_thread(_init_sync)
            security.load_allowed_paths(paths)
            logger.info("Whitelist cargada: %d rutas", len(paths))
        finally:
            # Se libera aunque falle: un tool call colgado esperando este
            # evento para siempre es peor que uno que falla con el error real.
            _db_ready.set()

    async def _check_self_update() -> None:
        # Corre en segundo plano, DESPUES del handshake MCP: un git fetch a
        # GitHub no puede demorar la conexion con Claude Code, que tiene su
        # propio timeout y da el servidor por caido si no responde a tiempo.
        try:
            estado = await asyncio.to_thread(self_update.check)
            local = estado.get("local", {})
            if estado.get("update_available"):
                logger.warning(
                    "HAY VERSION NUEVA DEL SERVER: %d commit(s) por delante en GitHub. "
                    "Corre check_server_version(update=true) y reinicia Claude Code.",
                    estado.get("behind", 0),
                )
            else:
                logger.info("Server en %s (%s)", local.get("commit", "?"), local.get("subject", ""))
        except Exception as exc:
            logger.info("No se pudo comprobar la version del server: %s", exc)

    async with stdio_server() as (read_stream, write_stream):
        asyncio.create_task(_startup_db_init())
        asyncio.create_task(_check_self_update())
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
