import os
from dotenv import load_dotenv

load_dotenv()

# DeepSeek
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096"))
DEEPSEEK_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))
# Limite de caracteres del fallback en crudo cuando DeepSeek no responde
COMPRESS_FALLBACK_MAX_CHARS = int(os.getenv("COMPRESS_FALLBACK_MAX_CHARS", "12000"))

# GitHub
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# PostgreSQL
DATABASE_URL = os.environ["DATABASE_URL"]

# Rutas locales
PROJECTS_BASE_PATH = os.environ["PROJECTS_BASE_PATH"]
CHROMA_PERSIST_PATH = os.environ["CHROMA_PERSIST_PATH"]

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Embeddings
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHROMA_COLLECTION = "code_chunks"

# Chunking
CHUNK_SIZE = 150   # lineas por chunk
CHUNK_OVERLAP = 20

# Retrieval
TOP_K_RESULTS = 8   # fragmentos a recuperar por query
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "1.2"))  # cosine distance máximo (0=idéntico, 2=opuesto). MiniLM NL->código suele dar 0.6-1.1
