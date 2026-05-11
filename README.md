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
Claude Code (laptop / Kali en SSD portable)
        |
        | MCP protocol via Tailscale
        v
openclaw-server (casa) — i7 3ra gen, 16GB DDR3, ethernet fija
        |
        | Indexador de codigo (CPU only, all-MiniLM-L6-v2)
        | ChromaDB persistente en disco
        |
        | fragmentos relevantes
        v
DeepSeek Flash API  ──────────────────────────────────────────► PostgreSQL (Railway)
                                                                         ^
                                                                         |
                                                                Next.js Dashboard (Railway)
```

1. Claude Code llama a `query_context` con una pregunta en lenguaje natural
2. El servidor busca fragmentos relevantes en ChromaDB por similitud semantica
3. Los fragmentos se envian a DeepSeek Flash para comprimir y filtrar
4. Claude Code recibe solo lo relevante — menos tokens, mismo contexto util
5. Cada operacion queda registrada en PostgreSQL con tokens y costo

---

## Tools MCP

| Tool | Descripcion |
|---|---|
| `query_context` | Consulta contexto de un proyecto por pregunta en lenguaje natural |
| `index_project` | Re-indexa un proyecto existente en disco |
| `list_projects` | Lista los proyectos disponibles |
| `clone_project` | Clona un repo de GitHub e indexa automaticamente |

---

## Stack

| Capa | Tecnologia |
|---|---|
| Protocolo | MCP SDK (Python) |
| Embeddings | sentence-transformers — all-MiniLM-L6-v2 (CPU) |
| Vector store | ChromaDB con PersistentClient |
| Compresion de contexto | DeepSeek Flash via SDK Anthropic |
| Clonado de repos | GitPython |
| Base de datos | PostgreSQL en Railway |
| Acceso remoto | Tailscale |

---

## Instalacion

```bash
git clone https://github.com/mickaell/mcp-context-backend
cd mcp-context-backend/server

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y completa las variables:

```bash
cp .env.example .env
```

Aplica el schema en tu PostgreSQL de Railway:

```bash
psql $DATABASE_URL -f ../sql/schema.sql
```

Levanta el servidor:

```bash
python main.py
```

---

## Configuracion en Claude Code

```bash
claude mcp add --scope user mcp-context -- /ruta/a/server/.venv/bin/python /ruta/a/server/main.py
```

---

## Variables de entorno

| Variable | Descripcion |
|---|---|
| `DEEPSEEK_API_KEY` | API key de DeepSeek |
| `GITHUB_TOKEN` | Token de GitHub (scope: `repo`) para repos privados |
| `DATABASE_URL` | PostgreSQL en Railway (usar la URL publica) |
| `PROJECTS_BASE_PATH` | Directorio base donde se clonan los repos |
| `CHROMA_PERSIST_PATH` | Directorio donde ChromaDB guarda los vectores en disco |
| `LOG_LEVEL` | Nivel de log (INFO por defecto) |

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
- Bloquea archivos sensibles: `.env`, `.pem`, `.key`, `secrets.json`, etc.
- No ejecuta ningun comando del sistema — la unica excepcion es `git clone` via GitPython
- No expuesto a internet — acceso exclusivo via Tailscale
