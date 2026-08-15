import os
import socket
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
# DeepSeek retira nombres de modelo sin avisar: "deepseek-chat" quedo invalido y
# la API responde 400, lo que hacia caer todas las llamadas al fallback de chunks
# crudos (coste 0, summary vacio) sin que se notara. Configurable por entorno.
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
# Presupuesto de salida. En los modelos v4 el bloque `thinking` SALE DE AQUI: con
# 4096 el modelo se quedaba sin presupuesto razonando sobre un lote grande del
# audit y devolvia una respuesta sin texto, que caia al fallback de chunks crudos
# (categoria con 0 tokens y sin hallazgos, indistinguible de "todo limpio").
DEEPSEEK_MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "16384"))
DEEPSEEK_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))
# Timeout por request. Ojo al subir max_tokens: generar mas texto tarda mas, y un
# timeout corto lo corta a mitad y dispara reintentos que vuelven a tardar.
DEEPSEEK_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT", "120.0"))
# Limite de caracteres del fallback en crudo cuando DeepSeek no responde
COMPRESS_FALLBACK_MAX_CHARS = int(os.getenv("COMPRESS_FALLBACK_MAX_CHARS", "12000"))

# Precios USD por 1M tokens. Desde 2026-08-16 DeepSeek factura por franja horaria:
# off-peak cuesta la mitad que peak. Las horas peak se declaran en UTC (01:00-04:00
# y 06:00-10:00); en hora de Ecuador (UTC-5) eso cae en 20:00-23:00 y 01:00-05:00,
# o sea que trabajando de dia siempre es off-peak.
# En env vars y no hardcodeados: los precios ya cambiaron una vez y volveran a hacerlo.
DEEPSEEK_PRICE_IN_OFFPEAK = float(os.getenv("DEEPSEEK_PRICE_IN_OFFPEAK", "0.22"))
DEEPSEEK_PRICE_OUT_OFFPEAK = float(os.getenv("DEEPSEEK_PRICE_OUT_OFFPEAK", "0.66"))
DEEPSEEK_PRICE_IN_PEAK = float(os.getenv("DEEPSEEK_PRICE_IN_PEAK", "0.44"))
DEEPSEEK_PRICE_OUT_PEAK = float(os.getenv("DEEPSEEK_PRICE_OUT_PEAK", "1.32"))
# Rangos horarios peak en UTC, como "inicio-fin" (fin exclusivo) separados por coma.
DEEPSEEK_PEAK_HOURS_UTC = os.getenv("DEEPSEEK_PEAK_HOURS_UTC", "1-4,6-10")

# GitHub
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# PostgreSQL
DATABASE_URL = _require("DATABASE_URL")

# Rutas locales
PROJECTS_BASE_PATH = _require("PROJECTS_BASE_PATH")
CHROMA_PERSIST_PATH = _require("CHROMA_PERSIST_PATH")

# Identificador de este dispositivo. Varios equipos comparten la misma Postgres,
# asi que cada proyecto guarda una ruta local por dispositivo. Si no se define
# DEVICE_ID en el entorno, se usa el hostname (estable en la mayoria de equipos).
DEVICE_ID = os.getenv("DEVICE_ID") or socket.gethostname()

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

# Batching del audit: las categorías estructurales (accessibility, theming) cargan
# TODOS los chunks de components/app, que en un repo grande superan la ventana del
# modelo → error 400. audit_batches parte los chunks en lotes que no excedan este
# presupuesto de caracteres (~4 chars/token) y los audita en paralelo.
# Bajado de 120K a 45K (≈11K tokens por lote): con lotes de 30K tokens el modelo
# razonaba tanto sobre el código que agotaba max_tokens ANTES de escribir los
# hallazgos, y el lote se perdía entero. Lotes más chicos = razonamiento más corto
# y acotado. Antes bajarlo costaba tiempo (más llamadas en serie); desde que los
# lotes van en paralelo es gratis: se reparten entre los workers.
AUDIT_BATCH_MAX_CHARS = int(os.getenv("AUDIT_BATCH_MAX_CHARS", "45000"))
# Tope total de chunks por categoría (0 = sin tope, audita todo en lotes). Subilo
# o capalo para controlar el costo en repos grandes; con 0 prioriza recall.
AUDIT_MAX_CHUNKS = int(os.getenv("AUDIT_MAX_CHUNKS", "0"))
# Llamadas a DeepSeek en paralelo durante un audit. El trabajo es I/O de red puro
# (10-13 categorías x N lotes en serie tardaban minutos), así que se reparten en
# hilos. ponytail: sin rate-limiter propio — el techo es el rate limit de DeepSeek;
# si empiezan a caer 429, _call ya reintenta con backoff, pero el camino de upgrade
# es un limitador de tokens/minuto compartido entre workers.
AUDIT_CONCURRENCY = max(1, int(os.getenv("AUDIT_CONCURRENCY", "4")))
# Presupuesto de salida para el perfil de describe_project. Necesita mas que el
# default porque son 5 secciones de prosa Y el modelo razona antes de escribir:
# con 4096 gastaba todo el presupuesto en el bloque `thinking` y devolvia una
# respuesta sin texto (que _call reportaba como "DeepSeek no disponible").
PROFILE_MAX_TOKENS = int(os.getenv("PROFILE_MAX_TOKENS", "12000"))
# Presupuesto TOTAL de caracteres del audit en modo raw (todas las categorías
# juntas). Sin tope, un repo mediano devuelve 300K+ chars que saturan el contexto
# del modelo que llama. Al agotarse, las categorías restantes indican pedirse
# solas con categories=[...]. 0 = sin tope.
AUDIT_RAW_MAX_CHARS = int(os.getenv("AUDIT_RAW_MAX_CHARS", "150000"))
# Segunda pasada de verificación de hallazgos CRÍTICO/ALTO contra el archivo
# completo citado (anti-falsos-positivos). Una llamada DeepSeek extra por audit.
AUDIT_VERIFY_ENABLED = os.getenv("AUDIT_VERIFY_ENABLED", "true").lower() != "false"
MAX_DISTANCE = float(os.getenv("MAX_DISTANCE", "1.2"))  # cosine distance máximo (0=idéntico, 2=opuesto). MiniLM NL->código suele dar 0.6-1.1

# Reranking híbrido (semántico + léxico) post-retrieval. Recupera un pool más
# grande de Chroma y lo reordena mezclando distancia vectorial con overlap de
# tokens — barato, sin deps nuevas, y rescata matches exactos (nombres de símbolo,
# flags) que el embedding NL->código suele perder.
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() != "false"
RERANK_CANDIDATE_MULT = int(os.getenv("RERANK_CANDIDATE_MULT", "3"))  # pool = top_k * mult
RERANK_W_SEM = float(os.getenv("RERANK_W_SEM", "0.7"))  # peso del score semántico
RERANK_W_LEX = float(os.getenv("RERANK_W_LEX", "0.3"))  # peso del score léxico
