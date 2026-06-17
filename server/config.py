import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"Falta la variable de entorno requerida: {key}. "
            f"Configurala en .env o en el entorno antes de iniciar el servidor."
        )
    return val


# DeepSeek
DEEPSEEK_API_KEY = _require("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096"))
DEEPSEEK_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "60.0"))
# Limite de caracteres del fallback en crudo cuando DeepSeek no responde
COMPRESS_FALLBACK_MAX_CHARS = int(os.getenv("COMPRESS_FALLBACK_MAX_CHARS", "12000"))

# GitHub
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# PostgreSQL
DATABASE_URL = _require("DATABASE_URL")

# Rutas locales
PROJECTS_BASE_PATH = _require("PROJECTS_BASE_PATH")
CHROMA_PERSIST_PATH = _require("CHROMA_PERSIST_PATH")

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Embeddings
# Por defecto all-MiniLM-L6-v2 (rápido, general). Para mejor recall sobre código
# considerar un modelo code-aware: "jinaai/jina-embeddings-v2-base-code" o
# "nomic-ai/nomic-embed-text-v1.5". OJO: cambiar el modelo cambia la dimensión de
# los vectores e invalida el índice Chroma existente — exige reindex FULL de todos
# los proyectos (index_project sin incremental).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
CHROMA_COLLECTION = "code_chunks"

# Chunking
CHUNK_SIZE = 150   # lineas por chunk
CHUNK_OVERLAP = 20

# Retrieval
TOP_K_RESULTS = 8   # fragmentos a recuperar por query (query_context normal)
# Las auditorías priorizan recall sobre costo: barren más chunks por categoría.
AUDIT_TOP_K = int(os.getenv("AUDIT_TOP_K", "18"))

# Batching del audit: deepseek-chat tiene ~64K tokens de ventana. Las categorías
# estructurales (accessibility, theming) cargan TODOS los chunks de components/app,
# que en un repo grande superan la ventana → error 400. audit_context parte los
# chunks en lotes que no excedan este presupuesto de caracteres (~4 chars/token,
# 120K chars ≈ 30K tokens, deja margen para system + output). Una categoría con más
# chunks se audita en varias pasadas y se concatenan los hallazgos.
AUDIT_BATCH_MAX_CHARS = int(os.getenv("AUDIT_BATCH_MAX_CHARS", "120000"))
# Tope total de chunks por categoría (0 = sin tope, audita todo en lotes). Subilo
# o capalo para controlar el costo en repos grandes; con 0 prioriza recall.
AUDIT_MAX_CHUNKS = int(os.getenv("AUDIT_MAX_CHUNKS", "0"))
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "1.2"))  # cosine distance máximo (0=idéntico, 2=opuesto). MiniLM NL->código suele dar 0.6-1.1

# Reranking híbrido (semántico + léxico) post-retrieval. Recupera un pool más
# grande de Chroma y lo reordena mezclando distancia vectorial con overlap de
# tokens — barato, sin deps nuevas, y rescata matches exactos (nombres de símbolo,
# flags) que el embedding NL->código suele perder.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() != "false"
RERANK_CANDIDATE_MULT = int(os.getenv("RERANK_CANDIDATE_MULT", "3"))  # pool = top_k * mult
RERANK_W_SEM = float(os.getenv("RERANK_W_SEM", "0.7"))  # peso del score semántico
RERANK_W_LEX = float(os.getenv("RERANK_W_LEX", "0.3"))  # peso del score léxico
