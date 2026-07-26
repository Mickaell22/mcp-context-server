from __future__ import annotations

import json
import os
from datetime import datetime

import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from config import DATABASE_URL, DEVICE_ID


def get_connection():
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def cursor():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- schema / multi-dispositivo ----------

def ensure_schema() -> None:
    """Migraciones idempotentes sobre la Postgres compartida.

    Varios equipos comparten esta Postgres; cada uno corre el server y ejecuta
    esto al arrancar. ADD COLUMN IF NOT EXISTS es seguro y no destructivo.

    - projects.device_paths: ruta local del proyecto por dispositivo.
    - indexed_files.device_id: DE QUE dispositivo es ese estado indexado. Los
      vectores viven en un ChromaDB local por equipo, asi que los hashes que
      alimentan el delta indexing describen el indice de UN equipo, no un hecho
      global. Sin esta columna, el equipo A marcaba los archivos como indexados
      y el equipo B se saltaba el reindexado creyendolos al dia, sirviendo
      codigo viejo (o nada) desde su Chroma.
    - projects.device_indexed_at: cuando indexo CADA equipo, para que
      list_projects no muestre como reciente el indexado de otra maquina.
    """
    with cursor() as cur:
        cur.execute(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "device_paths JSONB NOT NULL DEFAULT '{}'::jsonb"
        )
        cur.execute(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "device_indexed_at JSONB NOT NULL DEFAULT '{}'::jsonb"
        )
        cur.execute("ALTER TABLE indexed_files ADD COLUMN IF NOT EXISTS device_id TEXT")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_indexed_files_device "
            "ON indexed_files(project_id, device_id)"
        )


def claim_local_paths(device_id: str) -> int:
    """Adopta para este dispositivo las rutas legacy que existan en ESTE disco.

    Para cada proyecto sin entrada para device_id cuyo `path` legacy apunte a un
    directorio existente localmente, guarda device_paths[device_id] = path. Nunca
    reclama rutas que no existen aca (asi no se atribuye la ruta de otro equipo).
    Devuelve cuantos proyectos reclamo.
    """
    claimed = 0
    with cursor() as cur:
        cur.execute("SELECT id, path, device_paths FROM projects")
        rows = cur.fetchall()
        for r in rows:
            dp = r["device_paths"] or {}
            if device_id in dp:
                continue
            p = r["path"]
            if p and os.path.isdir(p):
                cur.execute(
                    "UPDATE projects SET device_paths = device_paths || %s::jsonb WHERE id = %s",
                    (json.dumps({device_id: os.path.realpath(p)}), r["id"]),
                )
                claimed += 1
    return claimed


def set_device_path(project_id: int, device_id: str, path: str) -> None:
    """Registra/actualiza la ruta local de un proyecto para un dispositivo."""
    with cursor() as cur:
        cur.execute(
            "UPDATE projects SET device_paths = device_paths || %s::jsonb WHERE id = %s",
            (json.dumps({device_id: os.path.realpath(path)}), project_id),
        )


def resolve_project_path(project: dict, device_id: str) -> str:
    """Ruta local del proyecto para este dispositivo, con fallback al path legacy."""
    dp = project.get("device_paths") or {}
    return dp.get(device_id) or project["path"]


# ---------- projects ----------

def get_all_projects() -> list[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT id, name, path, device_paths, device_indexed_at, repo_url, last_indexed_at "
            "FROM projects ORDER BY name"
        )
        return [dict(r) for r in cur.fetchall()]


def get_project_by_name(name: str) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM projects WHERE name = %s", (name,))
        row = cur.fetchone()
        return dict(row) if row else None


def insert_project(name: str, path: str, repo_url: str | None = None) -> int:
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (name, path, repo_url, cloned_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (name) DO UPDATE SET path = EXCLUDED.path, repo_url = EXCLUDED.repo_url
            RETURNING id
            """,
            (name, path, repo_url),
        )
        return cur.fetchone()["id"]


def update_last_indexed(project_id: int, device_id: str = DEVICE_ID) -> None:
    """Marca el proyecto como indexado ahora, global y para ESTE dispositivo.

    last_indexed_at queda como "alguien lo indexo" (compat); device_indexed_at
    es el dato util: el indice de Chroma es local, asi que solo cuenta cuando lo
    indexo esta maquina.
    """
    with cursor() as cur:
        cur.execute(
            "UPDATE projects SET last_indexed_at = NOW(), "
            "device_indexed_at = device_indexed_at || %s::jsonb WHERE id = %s",
            (json.dumps({device_id: datetime.now().isoformat()}), project_id),
        )


def delete_project(project_id: int) -> None:
    """Borra el proyecto y todas sus filas dependientes en orden de FK.

    No hay ON DELETE CASCADE en el esquema, asi que se borra manualmente:
    queries y blocked_attempts (via sessions) -> sessions -> indexed_files
    -> file_imports -> projects. Todo en una sola transaccion.
    """
    with cursor() as cur:
        cur.execute(
            "DELETE FROM queries WHERE session_id IN (SELECT id FROM sessions WHERE project_id = %s)",
            (project_id,),
        )
        cur.execute(
            "DELETE FROM blocked_attempts WHERE session_id IN (SELECT id FROM sessions WHERE project_id = %s)",
            (project_id,),
        )
        cur.execute("DELETE FROM sessions WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM indexed_files WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM file_imports WHERE project_id = %s", (project_id,))
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))


# ---------- sessions ----------

def create_session(project_id: int) -> int:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (project_id) VALUES (%s) RETURNING id",
            (project_id,),
        )
        return cur.fetchone()["id"]


def close_session(session_id: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE sessions SET ended_at = NOW() WHERE id = %s",
            (session_id,),
        )


# ---------- queries ----------

def log_query(
    session_id: int,
    query_text: str,
    response_text: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    with cursor() as cur:
        cur.execute(
            """
            INSERT INTO queries
                (session_id, query_text, response_text,
                 deepseek_input_tokens, deepseek_output_tokens, deepseek_cost_usd)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (session_id, query_text, response_text, input_tokens, output_tokens, cost_usd),
        )


# ---------- indexed_files ----------

def log_indexed_files(project_id: int, files: list[dict], device_id: str = DEVICE_ID) -> None:
    """files: lista de {file_path, file_size, content_hash?}

    Reemplaza SOLO el estado de este dispositivo (mas las filas legacy sin
    device_id, que se consumen en el primer reindex). Antes borraba todas las
    filas del proyecto, con lo que cada equipo pisaba los hashes del otro.
    """
    with cursor() as cur:
        cur.execute(
            "DELETE FROM indexed_files WHERE project_id = %s "
            "AND (device_id = %s OR device_id IS NULL)",
            (project_id, device_id),
        )
        if files:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO indexed_files (project_id, device_id, file_path, file_size, content_hash) VALUES %s",
                [
                    (project_id, device_id, f["file_path"], f["file_size"], f.get("content_hash"))
                    for f in files
                ],
            )


def get_file_hashes(project_id: int, device_id: str = DEVICE_ID) -> dict[str, str]:
    """Retorna {file_path: content_hash} de lo indexado por ESTE dispositivo.

    Filtro estricto por device_id: las filas legacy (device_id NULL) se ignoran
    a proposito, porque no se sabe que Chroma describen. Asi el primer
    incremental de cada equipo tras la migracion se comporta como un full y
    reconstruye su indice local.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT file_path, content_hash FROM indexed_files "
            "WHERE project_id = %s AND device_id = %s AND content_hash IS NOT NULL",
            (project_id, device_id),
        )
        return {row["file_path"]: row["content_hash"] for row in cur.fetchall()}


# ---------- file_imports ----------

def log_file_imports(project_id: int, imports: list[dict]) -> None:
    """Reemplaza todos los imports del proyecto. imports: [{file_path, import_name}]"""
    with cursor() as cur:
        cur.execute("DELETE FROM file_imports WHERE project_id = %s", (project_id,))
        if imports:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO file_imports (project_id, file_path, import_name) VALUES %s",
                [(project_id, i["file_path"], i["import_name"]) for i in imports],
            )


def update_file_imports(project_id: int, file_paths: list[str], imports: list[dict]) -> None:
    """Actualiza imports solo para los archivos dados (modo incremental)."""
    if not file_paths:
        return
    with cursor() as cur:
        cur.execute(
            "DELETE FROM file_imports WHERE project_id = %s AND file_path = ANY(%s)",
            (project_id, file_paths),
        )
        if imports:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO file_imports (project_id, file_path, import_name) VALUES %s",
                [(project_id, i["file_path"], i["import_name"]) for i in imports],
            )


def find_files_importing(project_id: int, symbol: str) -> list[str]:
    """Retorna archivos que importan el simbolo dado (busqueda case-insensitive)."""
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT file_path FROM file_imports WHERE project_id = %s AND import_name ILIKE %s ORDER BY file_path",
            (project_id, f"%{symbol}%"),
        )
        return [row["file_path"] for row in cur.fetchall()]


# ---------- blocked_attempts ----------

def log_blocked_attempt(session_id: int | None, attempted_path: str, reason: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO blocked_attempts (session_id, attempted_path, reason) VALUES (%s, %s, %s)",
            (session_id, attempted_path, reason),
        )


# ---------- whitelist bootstrap ----------

def load_project_paths() -> list[str]:
    """Todas las rutas conocidas para la whitelist: el path legacy MAS las rutas
    por dispositivo de todos los equipos. Asi un proyecto registrado desde otra
    maquina sigue pasando el gate de whitelist (las queries leen de Chroma por
    project_id, no del disco)."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT p FROM (
                SELECT path AS p FROM projects WHERE path IS NOT NULL AND path <> ''
                UNION
                SELECT dp.value AS p
                FROM projects, jsonb_each_text(projects.device_paths) AS dp
            ) q
            """
        )
        return [row["p"] for row in cur.fetchall()]


# Las lecturas estructurales toleran filas legacy (device_id NULL): describen
# QUE archivos tiene el repo, no el estado del Chroma local, y un indice viejo
# sigue siendo mejor que devolver nada mientras el equipo no reindexa.
_DEVICE_FILTER = "(device_id = %s OR device_id IS NULL)"


def get_files_by_path_patterns(
    project_id: int, patterns: list[str], device_id: str = DEVICE_ID
) -> list[str]:
    """Retorna file_paths que coinciden con cualquiera de los patrones ILIKE."""
    if not patterns:
        return []
    with cursor() as cur:
        conditions = " OR ".join(["file_path ILIKE %s"] * len(patterns))
        cur.execute(
            f"SELECT DISTINCT file_path FROM indexed_files WHERE project_id = %s "
            f"AND {_DEVICE_FILTER} AND ({conditions}) ORDER BY file_path",
            [project_id, device_id, *patterns],
        )
        return [row["file_path"] for row in cur.fetchall()]


def get_file_extensions(project_id: int, device_id: str = DEVICE_ID) -> dict[str, int]:
    """Retorna {extension: count} de archivos indexados del proyecto."""
    with cursor() as cur:
        cur.execute(
            f"SELECT file_path FROM indexed_files WHERE project_id = %s AND {_DEVICE_FILTER}",
            (project_id, device_id),
        )
        counts: dict[str, int] = {}
        for row in cur.fetchall():
            ext = os.path.splitext(row["file_path"])[1].lower()
            counts[ext] = counts.get(ext, 0) + 1
        return counts
