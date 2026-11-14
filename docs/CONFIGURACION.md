# Configuracion

Todas las variables van en `server/.env` (plantilla: `.env.example` en la raiz). Las cuatro criticas se validan al arrancar.


| Variable | Descripcion |
|---|---|
| `DEEPSEEK_API_KEY` | API key de DeepSeek |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_PROXY_KEY` | Opcionales, para redes que bloquean `api.deepseek.com`. Sin definir = conexion directa (default `https://api.deepseek.com/anthropic`). Con un proxy inverso (ej. un Cloudflare Worker): `DEEPSEEK_BASE_URL` apunta a el y `DEEPSEEK_PROXY_KEY` se envia como header `x-proxy-key`. |
| `GITHUB_TOKEN` | Token de GitHub (scope: `repo`) para repos privados |
| `DATABASE_URL` | PostgreSQL en Railway (URL publica para acceso externo) |
| `DEVICE_ID` | Identificador de este equipo (ej. `desktop`, `laptop`). Como varios dispositivos comparten la misma Postgres, cada proyecto guarda una ruta local por dispositivo (`projects.device_paths`). Si se omite, se usa el hostname. Ver [Varios equipos](MULTI-EQUIPO.md). |
| `PROJECTS_BASE_PATH` | Directorio base donde se clonan los repos |
| `CHROMA_PERSIST_PATH` | Directorio donde ChromaDB guarda los vectores en disco |

<details>
<summary><b>Variables opcionales (todas con default)</b></summary>

| Variable | Descripcion |
|---|---|
| `LOG_LEVEL` | Nivel de log — `INFO` por defecto |
| `MAX_DISTANCE` | Umbral de distancia coseno para filtrar chunks (default: `1.2`). Con `all-MiniLM-L6-v2`, queries en lenguaje natural contra codigo suelen dar distancias de 0.6–1.1; valores menores a 1.0 filtran demasiado y devuelven contexto vacio. |
| `DEEPSEEK_MODEL` | Modelo de DeepSeek (default: `deepseek-flash`, V4.1-Flash; el otro es `deepseek-v4-pro`. `deepseek-v4-flash` esta retirado y DeepSeek lo redirige a `deepseek-flash`). DeepSeek retira nombres viejos sin avisar y la API devuelve **400**: `deepseek-chat` ya no existe. Un 400 aqui NO rompe la tool — cae al fallback de chunks crudos con `total_tokens: 0` y `summary` vacio, que se parece a una auditoria limpia. Si un `audit_project` sale sospechosamente vacio, revisa `total_tokens` antes de creerle. |
| `DEEPSEEK_TIMEOUT` | Timeout en segundos para llamadas a DeepSeek (default: `120.0`). El SDK Anthropic usa 10 min por defecto, demasiado para una tool MCP. Ojo al bajarlo si subes `DEEPSEEK_MAX_TOKENS`: generar mas texto tarda mas, y un timeout corto lo corta a mitad y dispara reintentos. |
| `DEEPSEEK_MAX_TOKENS` | Presupuesto de salida por llamada (default: `32768`). **En los modelos v4 el bloque `thinking` sale de aqui**: con los 4096 anteriores, un lote grande del audit se quedaba sin presupuesto razonando y devolvia una respuesta sin texto, que caia al fallback de chunks crudos — una categoria con 0 tokens y sin hallazgos, indistinguible de "todo limpio". Con 16384 seguia pasando en la categoria `correctness`; desde 32768 corre entera. Si aun asi pasa, la respuesta trae `llm_available: false` y un `warning` en vez de parecer un audit limpio. |
| `EMBEDDING_MODEL` | Modelo SentenceTransformers para embeddings (default: `all-MiniLM-L6-v2`). Para mejor recall sobre codigo: `jinaai/jina-embeddings-v2-base-code` o `nomic-ai/nomic-embed-text-v1.5`. Cambiarlo invalida el indice Chroma (cambia la dimension del vector) y exige reindex **full** de todos los proyectos. |
| `AUDIT_TOP_K` | Nº de chunks que recupera cada categoria del audit (default: `18`). El audit prioriza recall sobre costo; `query_context` sigue usando `TOP_K_RESULTS=8`. |
| `AUDIT_BATCH_MAX_CHARS` | Presupuesto de caracteres por llamada a DeepSeek en el audit (default: `45000` ≈ 11K tokens). `audit_project` parte los chunks en lotes que no excedan este limite y los audita **en paralelo**. Bajado desde 120K: con lotes de ~30K tokens el modelo razonaba tanto sobre el codigo que agotaba `DEEPSEEK_MAX_TOKENS` antes de escribir los hallazgos y se perdia el lote entero. Desde que los lotes van en paralelo, tenerlos mas chicos ya no cuesta tiempo. |
| `AUDIT_MAX_CHUNKS` | Tope total de chunks por categoria del audit (default: `0` = sin tope, audita todo en lotes). Subilo a un entero para acotar costo en repos grandes a cambio de recall. |
| `AUDIT_RAW_MAX_CHARS` | Presupuesto TOTAL de caracteres de la respuesta del audit en modo `raw` (default: `150000`; `0` = sin tope). Evita respuestas de 300K+ chars que saturan el contexto del que llama; las categorias recortadas avisan y sugieren pedirse solas con `categories=[...]`. |
| `AUDIT_VERIFY_ENABLED` | Verificacion de hallazgos CRITICO/ALTO contra el archivo completo citado (default: `true`). Una llamada DeepSeek extra por audit; los descartados quedan en `summary.descartados` con su motivo. `false` la desactiva. |
| `DB_CONNECT_TIMEOUT` | Segundos para abrir la conexion a Postgres (default: `10`). Sin tope, un proxy de Railway que no responde dejaba la tool colgada. |
| `RETRIEVAL_TIMEOUT` | Tope en segundos de la recuperacion de `query_context` (default: `180`). Si se pasa, la tool devuelve un error con la etapa en curso (cargando modelo, abriendo Chroma) en vez de colgarse. |
| `AUDIT_CONCURRENCY` | Llamadas a DeepSeek en paralelo dentro de un audit (default: `4`). El audit son N categorias x M lotes de I/O de red; en serie tardaba minutos en repos grandes. Subirlo mucho puede disparar rate limits (429); `1` vuelve al modo secuencial. |
| `PROFILE_MAX_TOKENS` | Presupuesto de salida de `describe_project` (default: `12000`). Necesita mas que `DEEPSEEK_MAX_TOKENS` porque el perfil son 5 secciones de prosa **y** el modelo razona antes de escribir: con 4096 gastaba todo el presupuesto en el bloque `thinking` y devolvia una respuesta sin texto. |
| `RERANK_ENABLED` | Activa el reranking híbrido semántico+léxico post-retrieval (default: `true`). `false` lo desactiva. |
| `RERANK_CANDIDATE_MULT` / `RERANK_W_SEM` / `RERANK_W_LEX` | Tamaño del pool de candidatos (`top_k × mult`, default 3) y pesos del score (default 0.7 semántico / 0.3 léxico). |

</details>

Las variables criticas (`DEEPSEEK_API_KEY`, `DATABASE_URL`, `PROJECTS_BASE_PATH`, `CHROMA_PERSIST_PATH`) son validadas al arrancar el servidor: si falta alguna, el proceso falla con un `RuntimeError` que indica exactamente cual variable falta.

## Costos de DeepSeek

DeepSeek factura por franja horaria: off-peak cuesta la mitad que peak. Las horas peak son **01:00-04:00 y 06:00-10:00 UTC, solo de lunes a viernes** (el fin de semana es off-peak todo el dia), que en hora de Ecuador (UTC-5) caen en **20:00-23:00 y 01:00-05:00** — trabajando de dia siempre pagas off-peak. El calculo de costo elige la tarifa segun la hora UTC de cada llamada (`deepseek_client._rates()`) y cobra aparte la entrada que DeepSeek ya tenia en cache (`cache_read_input_tokens`). Las seis tarifas son env vars. Precios revisados el 2026-10-09:

| | Off-peak | Peak |
|---|---|---|
| Input cache miss (1M tokens) | $0.15 | $0.30 |
| Input cache hit (1M tokens) | $0.003 | $0.006 |
| Output (1M tokens) | $0.60 | $1.20 |
| Query promedio (~5k input, ~1k output) | ~$0.0014 | ~$0.0027 |
| Auditoria completa de un repo | ~$0.01 | ~$0.02 |

> Referencia de uso real: 152 llamadas en 26 dias (1.07M input + 161K output) cuestan **$0.26** off-peak o **$0.51** peak sin contar cache hits.
>
> El ratio input/output es de ~6.7:1, asi que lo que mas pesa es el **input**. El *cache hit* es 50x mas barato que el miss y DeepSeek lo aplica solo cuando el inicio del prompt se repite: el audit repite el mismo bloque de instrucciones en cada lote, asi que conviene que ese bloque vaya siempre primero.


---

[Volver al README](../README.md)
