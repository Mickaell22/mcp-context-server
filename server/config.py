import os
from dotenv import load_dotenv

load_dotenv()

# DeepSeek
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_MODEL = "deepseek-chat"

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
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "0.7"))  # cosine distance máximo (0=idéntico, 1=ortogonal)
