# MCP Context Server

Servidor MCP que actua como memoria inteligente de proyectos para Claude Code. Indexa codebases, responde queries con contexto relevante comprimido y registra metricas de uso. Reduce el consumo de tokens en Claude Code usando DeepSeek Flash como preprocesador barato en lugar de mandar archivos completos al contexto.

---

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![MCP](https://img.shields.io/badge/MCP-SDK-blueviolet?style=for-the-badge&logo=anthropic&logoColor=white)](https://modelcontextprotocol.io)
[![DeepSeek](https://img.shields.io/badge/DeepSeek-Flash-4D6BFE?style=for-the-badge&logo=deepseek&logoColor=white)](https://platform.deepseek.com)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-Vectores-FF6B35?style=for-the-badge&logo=databricks&logoColor=white)](https://trychroma.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Railway-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://railway.com)
[![Tailscale](https://img.shields.io/badge/Acceso-Tailscale-245EFF?style=for-the-badge&logo=tailscale&logoColor=white)](https://tailscale.com)

---

## Como funciona

```
Claude Code (cualquier maquina)
        |
        | MCP protocol (local o via Tailscale)
        v
Servidor MCP (local en Kali SSD o en openclaw-server)
        |
        | Indexador de codigo (CPU only, all-MiniLM-L6-v2)
        | ChromaDB persistente en disco
        |
        | fragmentos relevantes
        v
DeepSeek Flash API  ──────────────────────────────────────────► PostgreSQL (Railway)
                                                                         ^
                                                                         |
                                                                Next.js Dashboard
                                                                ia.novamicktools.com
```

1. Claude Code llama a `query_context` con una pregunta en lenguaje natural
2. El servidor busca fragmentos relevantes en ChromaDB por similitud semantica
3. Los fragmentos se envian a DeepSeek Flash para comprimir y filtrar
4. Claude Code recibe solo lo relevante — menos tokens, mismo contexto util
5. Cada operacion queda registrada en PostgreSQL con tokens y costo

---

## Tools MCP

| Tool | Parametros | Descripcion |
|---|---|---|
| `query_context` | `query`, `project` (str o list), `code_only` (bool) | Busqueda semantica con compresion DeepSeek. Soporta multi-proyecto. |
| `index_project` | `project` | Re-indexa un proyecto existente por nombre. Modo incremental por defecto. |
| `list_projects` | — | Lista todos los proyectos registrados en la BD. |
| `clone_project` | `repo_url` | Clona un repo de GitHub e indexa en un solo paso. |
| `register_project` | `path`, `name` (opcional) | Registra un path local sin clonar e indexa. |
| `get_file` | `project`, `file_path` | Retorna el contenido completo de un archivo del indice. |
| `audit_project` | `project`, `categories` (opcional) | Auditoria automatica de codigo. Autodetecta frontend vs backend. Categorias backend: security, code_quality, error_handling, deprecated, config_secrets, imports, io_operations, tests. Frontend: accessibility, performance, state_management, seo, component_design, error_handling, deprecated, tests, bundle_size, hydration, theming. Fallback: si no encuentra patrones via busqueda semantica, analiza los chunks mas relevantes con DeepSeek. |
| `find_usages` | `project`, `symbol` | Busca que archivos importan un simbolo o modulo especifico. |

---

## Stack

| Capa | Tecnologia |
|---|---|
| Protocolo | MCP SDK (Python) |
| Embeddings | sentence-transformers — all-MiniLM-L6-v2 (CPU) |
| Vector store | ChromaDB con PersistentClient (disco local) |
| Compresion de contexto | DeepSeek Flash via SDK Anthropic |
| Clonado de repos | GitPython |
| Grafo de imports | ast (Python) + regex (JS/TS) |
| Base de datos | PostgreSQL en Railway |
| Acceso remoto | Tailscale (openclaw-server) |
| Dashboard | Next.js en Railway → ia.novamicktools.com |

---

## Instalacion

```bash
git clone https://github.com/Mickaell22/mcp-context-server
cd mcp-context-server/server

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y completa las variables:

```bash
cp .env.example .env
```

Aplica el schema en tu PostgreSQL de Railway (solo la primera vez):

```bash
psql $DATABASE_URL -f ../sql/schema.sql
```

Levanta el servidor:

```bash
.venv/bin/python main.py
```

### Paths en openclaw-server

```
/home/mickaell/Escritorio/Proyectos MICKAELL/mcp-context-server/
└── server/
    ├── main.py              # Entry point
    ├── tools/                # Tools MCP
    │   ├── audit_project.py  # Auditoria con fallback DeepSeek
    │   ├── query_context.py  # Busqueda semantica
    │   ├── register_project.py
    │   ├── index_project.py
    │   └── ...
    ├── indexer.py            # Indexacion en ChromaDB
    ├── retriever.py          # Recuperacion semantica
    ├── deepseek_client.py    # Cliente DeepSeek Flash
    └── db.py                 # PostgreSQL (Railway)
```

---

## Configuracion en Claude Code

```bash
claude mcp add --scope user mcp-context -- /ruta/a/server/.venv/bin/python /ruta/a/server/main.py
```

Para verificar que esta corriendo:

```bash
claude mcp list
```

---

## Instalacion como servicio (systemd)

Para que arranque automaticamente en openclaw-server:

```ini
# /etc/systemd/system/mcp-context.service
[Unit]
Description=MCP Context Server
After=network.target

[Service]
User=mickaell
WorkingDirectory=/ruta/a/mcp-context-server/server
ExecStart=/ruta/a/server/.venv/bin/python main.py
Restart=on-failure
EnvironmentFile=/ruta/a/server/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable mcp-context
sudo systemctl start mcp-context
```

---

## Variables de entorno

| Variable | Descripcion |
|---|---|
| `DEEPSEEK_API_KEY` | API key de DeepSeek |
| `GITHUB_TOKEN` | Token de GitHub (scope: `repo`) para repos privados |
| `DATABASE_URL` | PostgreSQL en Railway (URL publica para acceso externo) |
| `PROJECTS_BASE_PATH` | Directorio base donde se clonan los repos |
| `CHROMA_PERSIST_PATH` | Directorio donde ChromaDB guarda los vectores en disco |
| `LOG_LEVEL` | Nivel de log — `INFO` por defecto |
| `MAX_DISTANCE` | Umbral de distancia coseno para filtrar chunks (default: `0.7`) |

---

## Costo estimado DeepSeek Flash

| | Precio |
|---|---|
| Input | $0.14 / 1M tokens |
| Output | $0.28 / 1M tokens |
| Query promedio (~5k input, ~1k output) | ~$0.001 |
| 1000 queries al mes | ~$1.00 |

---

## Seguridad

- Solo opera dentro de rutas explicitamente permitidas (whitelist dinamica desde PostgreSQL)
- Bloquea archivos sensibles: `.env`, `.pem`, `.key`, `secrets.json`, `CLAUDE.md`, etc.
- Extensiones indexadas: `.py .js .ts .jsx .tsx .java .go .rs .dart .kt .swift .rb .php .c .cpp .h .html .css .scss .md .json .yaml .sql .sh`
- No ejecuta ningun comando del sistema — unica excepcion es `git clone` via GitPython
- No expuesto a internet — acceso local o via Tailscale
