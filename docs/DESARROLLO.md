# Desarrollo

Para quien quiera tocar el codigo del server: estructura, stack, tests y evaluacion.

## Estructura del server

```
mcp-context-server/
└── server/
    ├── main.py              # Entry point
    ├── tools/                    # Tools MCP
    │   ├── audit_project.py      # Auditoria (llamadas a DeepSeek en paralelo)
    │   ├── describe_project.py   # Reconocimiento: patrones, paleta, interacciones
    │   ├── check_updates.py      # Frescura vs GitHub y vs el indice
    │   ├── query_context.py      # Busqueda semantica
    │   ├── register_project.py
    │   ├── index_project.py
    │   └── ...
    ├── indexer.py            # Indexacion en ChromaDB
    ├── retriever.py          # Recuperacion semantica
    ├── deepseek_client.py    # Cliente DeepSeek Flash
    └── db.py                 # PostgreSQL (Railway)
```

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

## Tests y evaluación

Dos capas, separadas a propósito:

**Capa 1 — recall de retrieval (determinista, gratis, CI-able).** `tests/test_retrieval_recall.py` indexa las fixtures con bugs sembrados (`tests/fixtures/fake_backend` + `fake_frontend`) en un ChromaDB efímero y verifica que el archivo de cada bug se recupere con la query de su categoría. No usa DeepSeek ni Postgres; mide chunking + embeddings + reranking. Se salta solo si no hay `sentence-transformers`.

```bash
cd server && ./.venv/bin/python -m pytest tests/ -q
```

**Capa 2 — recall end-to-end del audit (no-determinista, ~centavos).** `eval/run_eval.py` corre el audit completo (con `paired_with` para el contrato) contra las fixtures y mide cuántos bugs REPORTA de verdad, con un scorecard ✓/✗ por bug. También verifica los `must_not_flag`: código legítimo (locks, validación de input externo) que NO debe aparecer en los hallazgos — el termómetro de falsos positivos, clave para `over-engineering`. Usa DeepSeek + Postgres; correr a mano al afinar prompts/modelo/`AUDIT_TOP_K`, **no en CI**.

```bash
cd server && ./.venv/bin/python eval/run_eval.py
```

**Benchmark de latencia (`eval/bench.py`).** Mide sobre este mismo repo la latencia de una llamada a Postgres, `query_context` (3 queries fijas: segundos y tokens), `list_projects` (segundos y tamaño de la respuesta) y un incremental sin cambios. Sirve para comparar ramas: correrlo en cada una (por ejemplo con `git worktree`) y diffear los JSON. No escribe en `queries`.

```bash
cd server && ./.venv/bin/python eval/bench.py /tmp/bench.json
```

El *golden set* vive en `tests/fixtures/expected.json` (archivo, categoría, keywords, severidad por bug, más `must_not_flag` para los falsos positivos). Para medir recall hay que tener ground truth, y la única forma confiable es sembrar los bugs uno mismo: por eso las fixtures son sintéticas, no un repo real.

Junto a la Capa 1 corren los tests de las demás piezas, todos offline: `test_audit_parallel.py` (los lotes se reparten en la pool y se re-agrupan en orden), `test_check_updates.py` (repos git temporales de verdad para `index_stale`, y el guardarrail que impide el `git pull` sobre un repo con cambios sin commitear), `test_describe_project.py` (extractores deterministas: paleta, manifiestos, estructura, naming) y `test_git_client.py`. `test_device_index.py` es la excepción: necesita una Postgres real y solo corre con `MCP_TEST_DATABASE_URL` exportada.

## Re-indexar por script (sin iniciar el servidor)

Si se llama `indexer.index_project()` directamente desde un script, la whitelist en memoria esta vacia y `is_file_allowed()` rechaza todos los archivos — se indexan 0 archivos mientras el modo full borra los chunks previos. Hay que poblar la whitelist antes:

```python
import security, indexer
security.add_allowed_path("/ruta/al/proyecto")
indexer.index_project(project_id, "/ruta/al/proyecto")
```


---

[Volver al README](../README.md)
