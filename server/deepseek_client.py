from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import anthropic

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_MAX_RETRIES,
    DEEPSEEK_TIMEOUT,
    COMPRESS_FALLBACK_MAX_CHARS,
    AUDIT_BATCH_MAX_CHARS,
    AUDIT_CONCURRENCY,
    PROFILE_MAX_TOKENS,
    DEEPSEEK_PRICE_IN_OFFPEAK,
    DEEPSEEK_PRICE_OUT_OFFPEAK,
    DEEPSEEK_PRICE_IN_PEAK,
    DEEPSEEK_PRICE_OUT_PEAK,
    DEEPSEEK_PEAK_HOURS_UTC,
)

logger = logging.getLogger(__name__)

def _peak_ranges() -> list[tuple[int, int]]:
    """Parsea DEEPSEEK_PEAK_HOURS_UTC ("1-4,6-10"). Un rango mal escrito se ignora
    en vez de tumbar el server: equivocarse en una env var de precios no debe
    impedir responder queries — a lo sumo el costo registrado sale como off-peak."""
    ranges = []
    for part in DEEPSEEK_PEAK_HOURS_UTC.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ini, fin = (int(x) for x in part.split("-", 1))
            ranges.append((ini, fin))
        except ValueError:
            logger.warning("Rango horario invalido en DEEPSEEK_PEAK_HOURS_UTC: %r (ignorado)", part)
    return ranges


def _is_peak(now: datetime | None = None) -> bool:
    """True si la hora UTC cae en franja peak (el doble de precio)."""
    hour = (now or datetime.now(timezone.utc)).hour
    return any(ini <= hour < fin for ini, fin in _peak_ranges())


def _rates() -> tuple[float, float]:
    """(precio_input, precio_output) por TOKEN segun la franja horaria actual."""
    if _is_peak():
        return DEEPSEEK_PRICE_IN_PEAK / 1_000_000, DEEPSEEK_PRICE_OUT_PEAK / 1_000_000
    return DEEPSEEK_PRICE_IN_OFFPEAK / 1_000_000, DEEPSEEK_PRICE_OUT_OFFPEAK / 1_000_000

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=DEEPSEEK_TIMEOUT,
        )
    return _client


def _raw_fallback(chunks: list[dict]) -> str:
    """Concatena los chunks en crudo, truncados a un limite razonable.

    Se usa cuando DeepSeek no responde: query_context nunca debe romper si
    ChromaDB devolvio chunks validos.
    """
    fragments = "\n\n---\n\n".join(
        f"# {c['file_path']} (chunk {c['chunk_index']})\n{c['content']}"
        for c in chunks
    )
    if len(fragments) > COMPRESS_FALLBACK_MAX_CHARS:
        fragments = fragments[:COMPRESS_FALLBACK_MAX_CHARS] + "\n\n[...truncado...]"
    return (
        "[DeepSeek no disponible — se devuelven los fragmentos crudos sin comprimir]\n\n"
        + fragments
    )


AUDIT_SYSTEM_INSTRUCTIONS = (
    "Eres un auditor de código senior. Reportas SOLO hallazgos concretos y accionables, "
    "no consejos genéricos. Formato obligatorio por hallazgo, uno por línea:\n"
    "  **[SEVERIDAD]** `archivo:línea` — qué está mal y por qué, en una frase. Fix: cómo arreglarlo.\n"
    "SEVERIDAD ∈ {CRÍTICO, ALTO, MEDIO, BAJO}. Ordena de mayor a menor severidad.\n"
    "CRÍTICO se reserva para lo DEMOSTRABLE en el propio fragmento: pérdida de datos, "
    "brecha de seguridad o crash seguro. Si tu hallazgo depende de un supuesto sobre "
    "código que no ves, baja la severidad o no lo reportes.\n"
    "Los fragmentos son EXTRACTOS parciales de cada archivo: NUNCA reportes que 'falta' "
    "un import, una validación, un manejo de errores o una función — puede existir fuera "
    "del fragmento. Reporta solo lo que el código visible demuestra por sí mismo.\n"
    "No marques como bug los patrones idiomáticos correctos del framework en uso "
    "(ej. updates atómicos bajo lock, decisiones documentadas en comentarios del código).\n"
    "Cita solo el snippet mínimo necesario (nunca pegues funciones completas).\n"
    "Si un fragmento no tiene problemas de la categoría auditada, ignóralo.\n"
    "Si NO hay ningún hallazgo real, responde exactamente: 'Sin hallazgos.'\n"
    "No inventes líneas: si el fragmento no trae números de línea, cita `archivo` y el "
    "nombre de la función (jamás 'chunk N')."
)


def _build_fragments(chunks: list[dict]) -> str:
    """Construye el bloque de fragmentos para el prompt. Si el chunk trae start_line
    (metadata del índice nuevo), numera cada línea con su número real en el archivo
    para que el modelo cite archivo:línea con precisión. Si trae symbols, los anota."""
    parts: list[str] = []
    for c in chunks:
        start = c.get("start_line")
        end = c.get("end_line")
        symbols = c.get("symbols")
        meta = f"# {c['file_path']}"
        if start and end:
            meta += f" (líneas {start}-{end})"
        elif start:
            meta += f" (desde línea {start})"
        # Sin start_line (índice viejo) no se anuncia el chunk_index: el modelo
        # lo copiaba como cita ('archivo:chunk0') en vez de usar la función.
        if symbols:
            meta += f" — define: {symbols}"

        body_lines = c["content"].splitlines()
        # el header inline "// ruta" es ruido para numerar; se omite si está
        if body_lines and body_lines[0].startswith("// "):
            body_lines = body_lines[1:]

        if start:
            body = "\n".join(f"{start + i}: {ln}" for i, ln in enumerate(body_lines))
        else:
            body = "\n".join(body_lines)

        parts.append(f"{meta}\n{body}")
    return "\n\n---\n\n".join(parts)


class _Unretryable(RuntimeError):
    """Fallo determinista: reintentarlo solo gasta tiempo y da el mismo error."""


def _call(prompt: str, chunks: list[dict], max_tokens: int | None = None) -> tuple[str, int, int, float]:
    """Llamada a DeepSeek con reintentos y fallback a chunks crudos. Compartida
    por compress_context, audit_context y profile_context."""
    client = _get_client()
    budget = max_tokens or DEEPSEEK_MAX_TOKENS
    last_error: Exception | None = None
    for attempt in range(DEEPSEEK_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=DEEPSEEK_MODEL,
                max_tokens=budget,
                messages=[{"role": "user", "content": prompt}],
            )
            # Los modelos v4 anteponen un bloque `thinking` al `text`, asi que
            # content[0] no siempre es texto: se concatenan solo los bloques de
            # texto. Leer content[0].text a ciegas tiraba AttributeError sobre
            # una respuesta 200 valida, y el except de abajo lo reportaba como
            # "DeepSeek no disponible".
            content = "".join(b.text for b in response.content if b.type == "text")
            if not content:
                blocks = [b.type for b in response.content]
                # Caso real: con un prompt que pide una respuesta larga, el modelo
                # gasta TODO max_tokens razonando y corta antes de emitir texto.
                # Es determinista: reintentarlo tres veces solo tarda 2 minutos mas
                # en dar el mismo resultado. Se sube el presupuesto, no se reintenta.
                if getattr(response, "stop_reason", None) == "max_tokens":
                    raise _Unretryable(
                        f"el modelo agoto max_tokens={budget} razonando y no llego a "
                        f"escribir la respuesta (bloques: {blocks}). Subi DEEPSEEK_MAX_TOKENS "
                        f"o acorta lo que se le pide."
                    )
                raise ValueError(f"Respuesta sin bloque de texto (bloques: {blocks})")
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            rate_in, rate_out = _rates()
            cost = (input_tokens * rate_in) + (output_tokens * rate_out)
            logger.debug("DeepSeek: %d in / %d out tokens, $%.6f", input_tokens, output_tokens, cost)
            return content, input_tokens, output_tokens, cost
        except _Unretryable as e:
            last_error = e
            break
        except Exception as e:
            last_error = e
            if attempt < DEEPSEEK_MAX_RETRIES:
                backoff = 2 ** attempt
                logger.warning(
                    "DeepSeek fallo (intento %d/%d): %s — reintentando en %ds",
                    attempt + 1, DEEPSEEK_MAX_RETRIES + 1, e, backoff,
                )
                time.sleep(backoff)
    logger.error(
        "DeepSeek no disponible (%s). Devolviendo chunks crudos.", last_error,
    )
    return _raw_fallback(chunks), 0, 0, 0.0


def _batch_by_chars(chunks: list[dict], max_chars: int) -> list[list[dict]]:
    """Parte los chunks en lotes cuyo contenido sumado no exceda max_chars.
    deepseek-chat tiene ~64K tokens de ventana; sin esto una categoría estructural
    (accessibility, theming) en un repo grande supera el límite y la llamada falla.
    Un chunk individual que ya exceda el presupuesto va solo en su lote (no se parte:
    un chunk son <=CHUNK_SIZE líneas, siempre cabe)."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for c in chunks:
        clen = len(c.get("content", ""))
        if current and size + clen > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(c)
        size += clen
    if current:
        batches.append(current)
    return batches


def _audit_prompt(instructions: str, batch: list[dict]) -> str:
    return (
        f"{AUDIT_SYSTEM_INSTRUCTIONS}\n\n"
        f"TAREA DE AUDITORÍA: {instructions}\n\n"
        f"Fragmentos de código a auditar:\n\n{_build_fragments(batch)}"
    )


def audit_batches(
    jobs: list[tuple[str, str, list[dict]]],
    max_workers: int | None = None,  # None -> AUDIT_CONCURRENCY, leido al llamar
) -> dict[str, tuple[str, int, int, float]]:
    """Audita varios trabajos en paralelo. jobs = [(key, instructions, chunks)];
    retorna {key: (findings, in_tok, out_tok, costo)}.

    La unidad de paralelismo es el LOTE, no el trabajo: una sola categoría
    estructural (accessibility carga todos los componentes) puede generar más
    lotes que todas las demás juntas, así que repartir por categoría dejaría a
    esa marcando el tiempo total. Se aplanan todos los lotes de todos los jobs en
    una sola pool y después se re-agrupan por key respetando el orden original,
    de modo que el resultado es idéntico al de la versión secuencial.
    """
    tasks: list[tuple[str, int, str, list[dict]]] = []  # (key, orden, prompt, batch)
    empty: dict[str, tuple[str, int, int, float]] = {}
    for key, instructions, chunks in jobs:
        if not chunks:
            empty[key] = ("Sin hallazgos.", 0, 0, 0.0)
            continue
        for order, batch in enumerate(_batch_by_chars(chunks, AUDIT_BATCH_MAX_CHARS)):
            tasks.append((key, order, _audit_prompt(instructions, batch), batch))

    if not tasks:
        return empty

    # El sleep del backoff de _call bloquea su propio worker, no al resto.
    workers = max(1, min(max_workers or AUDIT_CONCURRENCY, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda t: _call(t[2], t[3]), tasks))

    grouped: dict[str, list[tuple[int, str, int, int, float]]] = {}
    for (key, order, _, _), (text, in_tok, out_tok, cost) in zip(tasks, results):
        grouped.setdefault(key, []).append((order, text, in_tok, out_tok, cost))

    out = dict(empty)
    for key, parts in grouped.items():
        parts.sort(key=lambda p: p[0])
        findings = [p[1].strip() for p in parts if p[1] and p[1].strip() and p[1].strip() != "Sin hallazgos."]
        total_in = sum(p[2] for p in parts)
        total_out = sum(p[3] for p in parts)
        total_cost = sum(p[4] for p in parts)
        out[key] = ("\n".join(findings) if findings else "Sin hallazgos.", total_in, total_out, total_cost)
    return out


def audit_context(instructions: str, chunks: list[dict]) -> tuple[str, int, int, float]:
    """Variante de compress_context para auditorías: pide hallazgos estructurados
    con severidad, archivo:línea y fix. Retorna (findings, in_tok, out_tok, costo).

    Atajo de un solo trabajo sobre audit_batches (los lotes de este trabajo se
    paralelizan igual). Para auditar varias categorías, llamar a audit_batches
    directamente: reparte los lotes de TODAS ellas en la misma pool."""
    return audit_batches([("_", instructions, chunks)])["_"]


PROFILE_SYSTEM_INSTRUCTIONS = (
    "Eres un dev senior al que acaban de dar acceso a un repo ajeno y tiene que "
    "agregarle una feature SIN que se note que la escribio otra persona. Describe "
    "las convenciones que ya existen; no propongas mejoras ni critiques el codigo.\n"
    "Responde en español con EXACTAMENTE estas secciones y nada mas:\n"
    "## Arquitectura — que capas hay y quien llama a quien.\n"
    "## Unidad tipica — como se ve una unidad del proyecto (endpoint / componente / "
    "servicio / modelo): en que archivo vive, como se nombra, que trae siempre.\n"
    "## Flujo de datos — el camino de un dato desde que entra hasta que se guarda "
    "o se pinta.\n"
    "## Convenciones — nombres, imports, manejo de errores, estilos, lo que se "
    "repita en el codigo que ves.\n"
    "## Para agregar una feature — los pasos concretos y en orden que seguiria "
    "alguien que copia el patron existente, citando los archivos que tocaria.\n"
    "Se concreto y cita archivos reales. Los fragmentos son EXTRACTOS: si algo no "
    "se ve, no lo inventes ni digas que 'falta'. Los datos duros (dependencias, "
    "paleta, modulos mas importados) ya vienen medidos, no los recalcules: usalos "
    "para explicar, y no los repitas como lista."
)


def profile_context(facts: str, chunks: list[dict]) -> tuple[str, int, int, float]:
    """Sintesis de las convenciones de un proyecto para poder extenderlo.

    `facts` son los datos ya medidos sin LLM (stack, paleta, grafo de imports):
    van en el prompt como contexto para que la prosa explique en vez de adivinar.
    """
    if not chunks:
        return "", 0, 0, 0.0
    prompt = (
        f"{PROFILE_SYSTEM_INSTRUCTIONS}\n\n"
        f"DATOS MEDIDOS DEL PROYECTO (fiables, extraidos del indice):\n{facts}\n\n"
        f"Fragmentos representativos del codigo:\n\n{_build_fragments(chunks)}"
    )
    # Presupuesto propio: son 5 secciones de prosa y el modelo razona antes de
    # escribir. Con los 4096 de default gastaba todo el presupuesto pensando y
    # devolvia una respuesta SIN bloque de texto.
    return _call(prompt, chunks, max_tokens=PROFILE_MAX_TOKENS)


def compress_context(query: str, chunks: list[dict]) -> tuple[str, int, int, float]:
    """
    Envia los chunks a DeepSeek Flash para que filtre y comprima segun la query.
    Retorna: (contexto_comprimido, input_tokens, output_tokens, costo_usd)

    Si la API falla (timeout / 429 / 5xx) reintenta con backoff y, si agota los
    reintentos, devuelve un fallback con los chunks crudos (tokens y costo en 0)
    para que la query no se rompa cuando ChromaDB funciona correctamente.
    """
    if not chunks:
        return "", 0, 0, 0.0

    prompt = (
        f"El desarrollador pregunta: {query}\n\n"
        f"A continuacion hay fragmentos de codigo de su proyecto. "
        f"Extrae y resume SOLO lo relevante para responder su pregunta. "
        f"Si un fragmento no aporta nada, ignoralo. "
        f"Usa el mismo idioma que la pregunta. Se conciso.\n\n"
        f"{_build_fragments(chunks)}"
    )
    return _call(prompt, chunks)
