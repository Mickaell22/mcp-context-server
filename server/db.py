from __future__ import annotations

import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from config import DATABASE_URL


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


# ---------- projects ----------

def get_all_projects() -> list[dict]:
    with cursor() as cur:
        cur.execute("SELECT id, name, path, repo_url, last_indexed_at FROM projects ORDER BY name")
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


def update_last_indexed(project_id: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE projects SET last_indexed_at = NOW() WHERE id = %s",
            (project_id,),
        )


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

def log_indexed_files(project_id: int, files: list[dict]) -> None:
    """files: lista de {file_path, file_size, content_hash?}"""
    with cursor() as cur:
        cur.execute("DELETE FROM indexed_files WHERE project_id = %s", (project_id,))
        if files:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO indexed_files (project_id, file_path, file_size, content_hash) VALUES %s",
                [(project_id, f["file_path"], f["file_size"], f.get("content_hash")) for f in files],
            )


def get_file_hashes(project_id: int) -> dict[str, str]:
    """Retorna {file_path: content_hash} para todos los archivos indexados del proyecto."""
    with cursor() as cur:
        cur.execute(
            "SELECT file_path, content_hash FROM indexed_files WHERE project_id = %s AND content_hash IS NOT NULL",
            (project_id,),
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
    with cursor() as cur:
        cur.execute("SELECT path FROM projects")
        return [row["path"] for row in cur.fetchall()]
