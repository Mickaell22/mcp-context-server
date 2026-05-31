"""Configuracion comun de los tests.

Hace dos cosas antes de importar cualquier modulo del servidor:
1. Pone `server/` en sys.path (los modulos usan imports planos: `import db`, etc.).
2. Define variables de entorno dummy para que `config` importe sin un `.env`
   real ni conexion a DB/red. Ningun test toca Postgres ni DeepSeek.
"""

import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")
os.environ.setdefault("PROJECTS_BASE_PATH", "/tmp/mcp-test-projects")
os.environ.setdefault("CHROMA_PERSIST_PATH", "/tmp/mcp-test-chroma")
