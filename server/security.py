import os
from pathlib import Path
from config import PROJECTS_BASE_PATH

BLOCKED_EXTENSIONS = {".env", ".pem", ".key", ".cert", ".p12", ".pfx"}

BLOCKED_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "secrets.json", "credentials.json", "serviceAccount.json",
}

BLOCKED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".next", "dist", "build", ".pytest_cache",
}

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cs", ".go", ".rs",
    ".html", ".css",
    ".md", ".txt", ".json", ".yaml", ".yml",
    ".sql",
}

# Rutas permitidas — se cargan desde PostgreSQL al iniciar y se actualizan al clonar
_allowed_paths: set[str] = set()


def load_allowed_paths(paths: list[str]) -> None:
    _allowed_paths.clear()
    _allowed_paths.update(os.path.realpath(p) for p in paths)


def add_allowed_path(path: str) -> None:
    _allowed_paths.add(os.path.realpath(path))


def is_path_allowed(path: str) -> bool:
    real = os.path.realpath(path)
    return any(real == p or real.startswith(p + os.sep) for p in _allowed_paths)


def is_file_allowed(path: str) -> tuple[bool, str]:
    p = Path(path)

    if p.name in BLOCKED_FILENAMES:
        return False, f"archivo bloqueado: {p.name}"

    if p.suffix.lower() in BLOCKED_EXTENSIONS:
        return False, f"extension bloqueada: {p.suffix}"

    if p.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False, f"extension no permitida: {p.suffix}"

    if not is_path_allowed(path):
        return False, f"ruta fuera de la whitelist: {path}"

    return True, ""


def is_dir_blocked(name: str) -> bool:
    return name in BLOCKED_DIRS


def validate_project_path(path: str) -> tuple[bool, str]:
    real = os.path.realpath(path)
    base = os.path.realpath(PROJECTS_BASE_PATH)

    if not real.startswith(base + os.sep) and real != base:
        return False, f"ruta fuera de PROJECTS_BASE_PATH: {path}"

    if not os.path.isdir(real):
        return False, f"directorio no existe: {path}"

    return True, ""
