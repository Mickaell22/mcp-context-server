"""Reconocimiento de un proyecto: que patrones sigue y como se conecta.

Casi nunca se escribe algo de cero — se agrega a lo que ya existe. Esta tool
responde "¿como esta hecho esto?" antes de tocarlo, para que el codigo nuevo
salga igual al que ya hay.

Dos mitades, a proposito separadas en la respuesta:
  - `facts`: MEDIDO del indice, sin LLM. La paleta se cuenta con regex sobre el
    CSS y el grafo de imports sale de Postgres; preguntarselo a un modelo solo
    agregaria colores inventados y modulos que no existen.
  - `guide`: una pasada DeepSeek que EXPLICA los patrones (arquitectura, unidad
    tipica, flujo de datos, como agregar una feature). Es orientacion, no dato.

El resultado se cachea por dispositivo y se invalida solo cuando se reindexa.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter

import db
import deepseek_client
import retriever
import security
from config import DEVICE_ID
from tools.audit_project import _detect_project_type, _dedup

logger = logging.getLogger(__name__)

# ---------- paleta ----------

# Un `--primary: #2563eb` matchea _VAR_RE (para quedarse con el nombre del token)
# Y _HEX_RE (para contar el color): las dos pasadas son intencionales.
# En _HEX_RE el \b evita que #aabbccdd se lea como el #aabbcc de 6 digitos.
_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
_FUNC_RE = re.compile(r"\b(?:rgba?|hsla?|oklch|color-mix)\([^()]{0,120}\)")
# --token: valor  /  'token': valor  /  token: valor   (CSS vars y config de Tailwind)
_VAR_RE = re.compile(r"--([\w-]+)\s*:\s*([^;{}]+)")
_PALETTE_PATTERNS = ["%.css", "%.scss", "%.sass", "%tailwind.config%", "%theme%", "%colors%", "%tokens%"]
# Colores que no dicen nada de la identidad visual: aparecen en cualquier reset.
_TRIVIAL = {"#fff", "#ffffff", "#000", "#000000", "transparent", "inherit", "currentcolor"}


def _normalize_color(value: str) -> str:
    v = value.strip().lower()
    # #AABBCC y #abbc son el mismo color escrito distinto
    if v.startswith("#") and len(v) == 7:
        return v
    return re.sub(r"\s+", " ", v)


def extract_palette(chunks: list[dict], limit: int = 24) -> list[dict]:
    """Colores del proyecto ordenados por frecuencia de uso, con los tokens que
    los nombran. Determinista: se cuenta lo que hay escrito, no se interpreta."""
    counts: Counter[str] = Counter()
    tokens: dict[str, set[str]] = {}
    files: dict[str, set[str]] = {}

    for c in chunks:
        content = c.get("content", "")
        fp = c.get("file_path", "")

        # tokens declarados: --primary: #2563eb  ->  el color se queda con su nombre
        for name, raw in _VAR_RE.findall(content):
            for color in _HEX_RE.findall(raw) + _FUNC_RE.findall(raw):
                tokens.setdefault(_normalize_color(color), set()).add(f"--{name}")

        for color in _HEX_RE.findall(content) + _FUNC_RE.findall(content):
            key = _normalize_color(color)
            if key in _TRIVIAL:
                continue
            counts[key] += 1
            files.setdefault(key, set()).add(fp)

    return [
        {
            "color": color,
            "uses": n,
            "tokens": sorted(tokens.get(color, [])),
            "files": sorted(files.get(color, []))[:3],
        }
        for color, n in counts.most_common(limit)
    ]


# ---------- stack ----------

_MANIFEST_PATTERNS = [
    "%package.json", "%requirements.txt", "%pyproject.toml", "%pubspec.yaml",
    "%go.mod", "%Cargo.toml", "%pom.xml", "%build.gradle", "%composer.json", "%Gemfile",
]


def _parse_manifest(path: str, content: str) -> dict | None:
    """Dependencias declaradas. Solo se parsean los dos formatos que cubren casi
    todo lo del usuario; el resto va crudo a la sintesis, que sabe leerlos."""
    name = os.path.basename(path).lower()
    # El chunker antepone `// ruta` a cada chunk: rompe json.loads y se colaba
    # como si fuera una dependencia llamada "//".
    if content.startswith("// "):
        content = content.split("\n", 1)[1] if "\n" in content else ""
    if name == "package.json":
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
        return {
            "file": path,
            "runtime": sorted(data.get("dependencies", {})),
            "dev": sorted(data.get("devDependencies", {})),
            "scripts": sorted(data.get("scripts", {})),
        }
    if name == "requirements.txt":
        deps = [
            re.split(r"[=<>!~\[; ]", ln.strip())[0]
            for ln in content.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", "-"))
        ]
        return {"file": path, "runtime": sorted(d for d in deps if d), "dev": [], "scripts": []}
    return None


def _stack(project_id: int) -> dict:
    """Manifiestos del proyecto. El primer chunk de cada uno basta para las deps
    (package.json las declara arriba); pedir el archivo entero solo infla."""
    manifests = []
    raw_files = []
    for c in retriever.chunks_by_path_patterns(project_id, _MANIFEST_PATTERNS):
        parsed = _parse_manifest(c["file_path"], c["content"])
        if parsed:
            if not any(m["file"] == parsed["file"] for m in manifests):
                manifests.append(parsed)
        else:
            raw_files.append(c["file_path"])
    return {"manifests": manifests, "otros_manifiestos": sorted(set(raw_files))}


# ---------- estructura y nombres ----------

def _structure(file_paths: list[str], depth: int = 2, limit: int = 30) -> list[dict]:
    """Carpetas hasta `depth` niveles con su conteo de archivos."""
    counts: Counter[str] = Counter()
    for fp in file_paths:
        parts = fp.split("/")[:-1][:depth]
        if parts:
            counts["/".join(parts)] += 1
    return [{"dir": d, "files": n} for d, n in counts.most_common(limit)]


_PASCAL_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
_SNAKE_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


def _naming(file_paths: list[str]) -> dict:
    """Convencion dominante de nombres de archivo. Es lo primero que delata un
    archivo escrito por otra persona."""
    counts: Counter[str] = Counter()
    for fp in file_paths:
        stem = os.path.splitext(os.path.basename(fp))[0]
        if _PASCAL_RE.match(stem):
            counts["PascalCase"] += 1
        elif _KEBAB_RE.match(stem):
            counts["kebab-case"] += 1
        elif _SNAKE_RE.match(stem):
            counts["snake_case"] += 1
        else:
            counts["otro"] += 1
    dominant = counts.most_common(1)[0][0] if counts else None
    return {"dominant": dominant, "counts": dict(counts)}


# ---------- chunks representativos para la sintesis ----------

_FRONT_SAMPLE = ["%/components/%", "%/pages/%", "%/app/%", "%/hooks/%", "%/services/%", "%/api/%", "%/store/%"]
_BACK_SAMPLE = ["%/routers/%", "%/routes/%", "%/controllers/%", "%/services/%", "%/models/%", "%/schemas/%", "%/repositories/%"]
# Codigo que NO representa las convenciones del proyecto: mocks y fixtures imitan
# otro sistema a proposito. Caso real: en este mismo repo, tests/fixtures/fake_backend/
# tiene routers/ y se llevaba todo el muestreo.
_SAMPLE_EXCLUDE = ("/tests/", "/test/", "/fixtures/", "/__mocks__/", "/mocks/", "/e2e/")
# Tope de fragmentos que van al modelo: es un perfil, no una auditoria.
_SAMPLE_MAX = 18


def _without_tests(chunks: list[dict]) -> list[dict]:
    return [c for c in chunks if not any(x in f"/{c['file_path']}" for x in _SAMPLE_EXCLUDE)]


def _sample_chunks(project_id: int, project_type: str, focus: str) -> list[dict]:
    """Un archivo representativo por carpeta clave, mas lo que pida `focus`.

    Se toma el PRIMER chunk de cada archivo (donde viven imports y firma) y como
    mucho dos archivos por patron: el objetivo es ver el patron repetido, no
    cubrir el repo entero."""
    chunks: list[dict] = []
    patterns = _FRONT_SAMPLE if project_type == "frontend" else _BACK_SAMPLE
    for pat in patterns:
        found = retriever.chunks_by_path_patterns(project_id, [pat], first_chunk_only=True)
        chunks.extend(_without_tests(found)[:2])

    if focus:
        chunks.extend(_without_tests(retriever.retrieve(focus, project_id, top_k=8, code_only=True)))

    if not chunks:
        # Ninguna carpeta convencional con codigo real: se cae a una vista general.
        # Ojo: el fallback es GLOBAL, no por patron. Si un patron solo matchea
        # fixtures, la conclusion correcta es que el proyecto NO tiene esa capa —
        # colar las fixtures igual hacia que la guia describiera una API FastAPI
        # que este repo no tiene (caso real auditando este mismo proyecto).
        chunks.extend(_without_tests(retriever.retrieve(
            "punto de entrada, modulos principales, configuracion, logica central",
            project_id, top_k=12, code_only=True,
        )))
    return _dedup(chunks)[:_SAMPLE_MAX]


def _facts_for_prompt(facts: dict) -> str:
    """Los datos medidos, compactos, para que la sintesis explique en vez de
    adivinar. Se recortan a lo que cabe sin comerse el prompt."""
    lines = [f"- Tipo detectado: {facts['project_type']}", f"- Archivos indexados: {facts['files_indexed']}"]
    for m in facts["stack"]["manifests"]:
        deps = ", ".join(m["runtime"][:30]) or "(ninguna)"
        lines.append(f"- Dependencias de {m['file']}: {deps}")
    if facts["stack"]["otros_manifiestos"]:
        lines.append(f"- Otros manifiestos: {', '.join(facts['stack']['otros_manifiestos'])}")
    dirs = ", ".join(f"{d['dir']} ({d['files']})" for d in facts["structure"][:12])
    lines.append(f"- Carpetas: {dirs}")
    lines.append(f"- Nombres de archivo: predomina {facts['naming']['dominant']}")
    core = ", ".join(f"{m['module']} (x{m['imported_by']})" for m in facts["imports"]["most_imported"][:12])
    lines.append(f"- Modulos mas importados: {core}")
    if facts["palette"]:
        pal = ", ".join(
            f"{p['color']}{'=' + p['tokens'][0] if p['tokens'] else ''} (x{p['uses']})"
            for p in facts["palette"][:12]
        )
        lines.append(f"- Paleta: {pal}")
    return "\n".join(lines)


async def handle(args: dict, session_id: int | None) -> dict:
    project_name = (args.get("project") or "").strip()
    if not project_name:
        return {"error": "Se requiere 'project'"}

    project = db.get_project_by_name(project_name)
    if not project:
        return {"error": f"Proyecto '{project_name}' no encontrado"}
    if not security.is_path_allowed(project["path"]):
        return {"error": f"Proyecto '{project_name}' no esta en la whitelist"}

    refresh = bool(args.get("refresh", False))
    focus = (args.get("focus") or "").strip()
    pid = project["id"]

    # El perfil describe lo que hay en el Chroma LOCAL: se sella con el indexado
    # de este equipo y se vence solo cuando se reindexa aca.
    stamp = (project.get("device_indexed_at") or {}).get(DEVICE_ID)
    if not refresh and not focus:
        cached = db.get_profile(pid)
        if cached and cached["generated_for"] == stamp:
            payload = cached["payload"]
            payload["cached"] = True
            payload["generated_at"] = cached["created_at"]
            # Lo que costo esta llamada, no lo que costo generarlo en su dia.
            payload["tokens_used"] = 0
            payload["cost_usd"] = 0.0
            return payload

    if retriever.projects_without_chunks([pid]):
        return {
            "error": f"'{project_name}' no tiene chunks en el Chroma de este equipo ({DEVICE_ID}). "
                     f"Corre index_project aca antes de pedir el perfil."
        }

    # ---- datos duros (sin LLM) ----
    # "%" = todos los archivos indexados; get_files_by_path_patterns filtra ILIKE.
    file_paths = db.get_files_by_path_patterns(pid, ["%"])
    project_type = _detect_project_type(pid)
    palette_chunks = retriever.chunks_by_path_patterns(pid, _PALETTE_PATTERNS)

    facts = {
        "project_type": project_type,
        "files_indexed": len(file_paths),
        "stack": _stack(pid),
        "structure": _structure(file_paths),
        "naming": _naming(file_paths),
        "imports": db.get_import_graph(pid),
        "palette": extract_palette(palette_chunks),
    }

    # ---- sintesis (una pasada DeepSeek) ----
    chunks = _sample_chunks(pid, project_type, focus)
    instructions = _facts_for_prompt(facts)
    if focus:
        instructions += f"\n- El desarrollador va a trabajar en: {focus}. Sesga la explicacion hacia esa zona."

    guide, in_tok, out_tok, cost = await asyncio.to_thread(
        deepseek_client.profile_context, instructions, chunks
    )

    if session_id is not None:
        db.log_query(
            session_id=session_id,
            query_text=f"[describe:{project_name}] {focus or 'perfil general'}",
            response_text=guide,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
        )

    result = {
        "project": project_name,
        "facts": facts,
        "guide": guide,
        "files_sampled": list(dict.fromkeys(c["file_path"] for c in chunks)),
        "cached": False,
        "focus": focus or None,
        "tokens_used": in_tok + out_tok,
        "cost_usd": round(cost, 6),
    }

    # in_tok == 0 => el LLM no corrio y `guide` son los chunks crudos del fallback,
    # no una guia. Se dice explicitamente en vez de dejar que un perfil roto pase
    # por bueno (los `facts` siguen valiendo: se miden sin LLM).
    if not in_tok:
        result["llm_available"] = False
        result["warning"] = (
            "DeepSeek no respondio: 'guide' trae fragmentos crudos, no la sintesis. "
            "Los datos de 'facts' son validos igual. Revisa el log del server."
        )

    # Un perfil con focus es a medida: no se cachea para no pisar el general.
    if not focus and in_tok:
        try:
            db.save_profile(pid, result, stamp)
        except Exception as exc:
            logger.warning("No se pudo cachear el perfil de %s: %s", project_name, exc)

    return result
