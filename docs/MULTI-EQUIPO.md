# Varios equipos con una sola base

Todo lo que hay que saber si usas mcp-context en mas de un equipo. Si usas uno solo, no necesitas leer esto.

## Que se comparte y que no

PostgreSQL es **compartido en la nube** entre todas las maquinas que usen el servidor. ChromaDB es **local de cada maquina** (`CHROMA_PERSIST_PATH`).

Consecuencias importantes:

- `list_projects` muestra completos los proyectos que esta maquina tiene en disco o en su Chroma; `indexed_here` distingue los que puede consultar. Los registrados solo en otro equipo van como nombres en `other_devices` (sus rutas de otra maquina no sirven aca y eran la mayor parte de la respuesta).
- `query_context`, `audit_project` y `get_file` solo funcionan para proyectos indexados **en esa maquina**. Un proyecto que aparece en la lista pero devuelve contexto vacio normalmente fue indexado en otra maquina — no esta corrupto. `query_context` lo dice explicitamente en un campo `warning` en vez de devolver un contexto vacio mudo.
- Para usar un proyecto en una nueva maquina, hay que re-indexarlo localmente aunque ya aparezca en la lista: `index_project` (si el path en Postgres coincide con la ruta local) o `register_project` (si el path es distinto).
- El delta indexing **no confia en el indexado de otro equipo**: los hashes se guardan por `device_id`, asi que el primer `index_project(incremental=true)` de cada maquina se comporta como un full y reconstruye su Chroma. Antes los hashes eran globales y el incremental respondia "sin cambios" mientras el indice local estaba vacio o viejo — el proyecto quedaba inconsultable ahi sin forma obvia de arreglarlo.
- **No usar `delete_project` para limpiar proyectos de otra maquina**: borra la fila de PostgreSQL compartido y rompe el indice en todas las maquinas que lo usen. `delete_project` es irreversible y afecta a todos.

## Rutas por dispositivo

Como varios equipos comparten la misma base en Railway pero tienen los repos en
rutas distintas, cada proyecto guarda **una ruta por dispositivo** en la columna
`projects.device_paths` (`{device_id: path}`). La columna `path` legacy queda
como fallback.

- Define `DEVICE_ID` distinto en el `.env` de cada equipo (ej. `desktop`, `laptop`).
- Al arrancar, el server aplica la migracion (idempotente) y **adopta** para este
  dispositivo las rutas legacy que existan en su disco (`claim_local_paths`).
- `register_project` sobre un proyecto ya registrado en otro equipo **no lo pisa**:
  solo agrega la ruta local de este dispositivo.
- `list_projects` devuelve la ruta de este dispositivo y un flag `on_this_device`;
  si es `false`, registra el proyecto aca con `register_project` apuntando a su
  ruta local.
- El **estado de indexado tambien es por dispositivo** (`indexed_files.device_id`,
  `projects.device_indexed_at`): los vectores viven en un Chroma local, asi que
  los hashes del delta indexing describen el indice de UNA maquina. `list_projects`
  expone `indexed_here` y un `last_indexed` que es el de ESTE equipo
  (`last_indexed_anywhere` para el global).
- `find_usages` es la excepcion: lee `file_imports` de Postgres, no de Chroma, asi
  que responde desde cualquier equipo.

## Mantener el server al dia en varios equipos

Cada equipo corre su propia copia del server desde su clon del repo. Cuando subis un cambio desde una maquina, las demas no se enteran solas — para eso esta `check_server_version`:

- **El aviso llega solo**: al arrancar, y despues como mucho cada 6 horas, el server compara su commit con el remoto. Si hay version nueva, `list_projects` incluye un bloque `server_update` con cuantos commits faltan y de que son. Como `list_projects` es la primera llamada al entrar a un proyecto, el aviso aparece sin pedirlo.
- **Para actualizar**: `check_server_version(update=true)` hace el `git pull --ff-only` y reinstala dependencias si el commit toco `requirements.txt`. Despues **reinicia Claude Code** — el proceso en marcha sigue ejecutando el codigo que ya tenia en memoria.
- El repo del server se localiza por la ruta del propio `self_update.py`, no por la base de datos: funciona aunque el server no este registrado como proyecto indexado en ese equipo.
- Si el repo tiene cambios sin commitear, no se hace pull (no se pisa trabajo en curso). Si el `pip install` falla, avisa de **no** reiniciar hasta resolverlo: arrancar con dependencias a medias te deja sin MCP justo cuando lo necesitas para diagnosticar.

> Recorda que un cambio en `db.py` afecta a la Postgres **compartida**: conviene actualizar todos los equipos, porque uno con la version vieja puede pisar datos del nuevo.


---

[Volver al README](../README.md)
