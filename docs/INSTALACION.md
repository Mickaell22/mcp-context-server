# Instalacion guiada (para Claude Code)

> Este archivo esta escrito para que **Claude Code lo lea y haga la instalacion**.
> Si sos una persona: abri Claude Code en la carpeta del repo y pedile
> *"Lee docs/INSTALACION.md e instala mcp-context-server"*.

Claude: segui los pasos en orden. Antes de cada comando que instale algo, cree
una base de datos o use `sudo`, decile al usuario que vas a hacer y por que.
No inventes valores: lo que no sepas, preguntalo.

---

## Paso 1. Elegir el modo (pregunta al usuario)

El server guarda dos cosas en sitios distintos:

- **Vectores (ChromaDB)**: siempre en el disco de cada equipo.
- **Registro de proyectos, hashes, grafo de imports y metricas (PostgreSQL)**:
  donde diga `DATABASE_URL`.

Lo unico que cambia entre modos es **donde vive esa Postgres**. El codigo es el
mismo; no hay ningun flag de modo.

Hace esta pregunta (con AskUserQuestion si la tenes):

> ¿Vas a usar mcp-context en **un solo equipo** o en **varios** que compartan los
> mismos proyectos?

| Respuesta | Modo | Postgres |
|---|---|---|
| Un solo equipo (o no sabe) | **Local** (recomendado para empezar) | En este mismo equipo, en `127.0.0.1` |
| Varios equipos | **Multi-equipo** | Una sola Postgres a la que lleguen todos (nube o un servidor de casa por Tailscale) |

Si duda, elegi **Local**: se puede pasar a multi-equipo despues sin perder nada
(ver "Pasar de local a multi-equipo" al final).

---

## Paso 2. Requisitos

Verifica (no instales sin avisar):

- `python3 --version` -> 3.11 o mas.
- `git --version`.
- Una API key de DeepSeek (`https://platform.deepseek.com`). Es obligatoria: el
  server la valida al arrancar. Pedisela al usuario; **nunca la escribas en un
  archivo versionado**, solo en `server/.env`.
- Opcional: un token de GitHub (scope `repo`) si va a indexar repos privados con
  `clone_project`.

---

## Paso 3. Codigo y dependencias

Desde la raiz del repo:

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
```

Siempre venv: Kali/Debian recientes bloquean `pip` global (PEP 668).

---

## Paso 4a. Postgres en modo LOCAL

Elegi la primera opcion que exista en el equipo:

1. **Docker o Podman** (lo mas limpio, no toca el sistema):

   ```bash
   # Genera una contraseña aleatoria y usala abajo y en DATABASE_URL.
   docker run -d --name mcp-context-db --restart unless-stopped \
     -e POSTGRES_PASSWORD=<contraseña-generada> -e POSTGRES_DB=mcp_context \
     -p 127.0.0.1:5432:5432 -v mcp-context-pg:/var/lib/postgresql/data \
     postgres:17
   ```

   El `127.0.0.1:` del `-p` importa: la base queda solo para este equipo, no
   expuesta en la red.

2. **Postgres del sistema** (si ya esta instalado o el usuario lo prefiere):
   crear un usuario y una base `mcp_context` y armar la URL con ellos.

`DATABASE_URL=postgresql://postgres:<contraseña>@127.0.0.1:5432/mcp_context`

## Paso 4b. Postgres en modo MULTI-EQUIPO

Todos los equipos apuntan a **la misma** `DATABASE_URL`. Opciones:

- Una Postgres administrada (Railway, Neon, Supabase...): usar su URL **publica**.
- Una Postgres en un servidor de casa, accesible por **Tailscale**
  (`postgresql://...@<ip-tailscale>:5432/mcp_context`). No abrirla a internet.

Ademas, en cada equipo:

- `DEVICE_ID` distinto y estable (`laptop`, `desktop`, `trabajo`). Si se omite
  se usa el hostname, que puede cambiar.
- Cada equipo tiene su propio Chroma: **un proyecto hay que indexarlo en cada
  equipo** donde se quiera consultar. `list_projects` lo dice con `indexed_here`.
- Al actualizar el server, actualizar **todos** los equipos
  (`check_server_version(update=true)`): uno con codigo viejo puede pisar datos
  de la base compartida.

---

## Paso 5. Archivo `.env`

```bash
cp ../.env.example .env      # estando en server/
```

Completar como minimo: `DEEPSEEK_API_KEY`, `DATABASE_URL`, `PROJECTS_BASE_PATH`
(donde `clone_project` clona repos) y `CHROMA_PERSIST_PATH` (donde se guardan los
vectores; una carpeta del usuario, fuera del repo). Crear ambas carpetas. El
resto tiene defaults razonables.

**No hace falta correr `sql/schema.sql` a mano**: el server lo aplica solo al
arrancar (es idempotente; en una base vacia crea todo, en una existente solo
agrega lo que falte).

---

## Paso 6. Registrar el server en Claude Code

```bash
claude mcp add --scope user mcp-context -- \
  "<ruta-absoluta>/server/.venv/bin/python" "<ruta-absoluta>/server/main.py"
```

En Windows el interprete es `server\.venv\Scripts\python.exe`.

**NixOS**: las ruedas de `psycopg2`/`torch` no encuentran `libz.so.1` /
`libstdc++` (`ImportError: libz.so.1`). Con `programs.nix-ld.enable = true` (y
`zlib` + `stdenv.cc.cc.lib` en `programs.nix-ld.libraries`), agregar
`-e LD_LIBRARY_PATH=/run/current-system/sw/share/nix-ld/lib` al `claude mcp add`.
Es la misma variable que hay que exportar para correr los tests a mano.

Despues: **reiniciar Claude Code** para que cargue el server.

---

## Paso 7. Verificar

En una sesion nueva de Claude Code:

1. `list_projects` -> responde (lista vacia en una base nueva). Si falla con un
   error de conexion, revisar `DATABASE_URL` y que la Postgres este corriendo.
2. `register_project(path=<un repo chico del usuario>)` -> indexa (solo lectura,
   no modifica el repo). La primera vez descarga el modelo de embeddings (~90 MB).
3. `query_context(project=<nombre>, query="como esta organizado el proyecto")`
   -> devuelve contexto con `tokens_used > 0`. Si sale `llm_available: false`, la
   API key de DeepSeek o la red estan mal.

Reportale al usuario el modo elegido, donde quedo la base y donde los vectores.

---

## Pasar de local a multi-equipo

Nada se pierde; los datos son los mismos:

1. Crear la Postgres compartida (Paso 4b).
2. `pg_dump <url-local> | psql <url-compartida>`
3. Cambiar `DATABASE_URL` en `server/.env` y reiniciar Claude Code.
4. En cada equipo nuevo: Pasos 3, 5 (con su propio `DEVICE_ID`) y 6, y despues
   `register_project` con la ruta local de cada proyecto que quiera consultar.
