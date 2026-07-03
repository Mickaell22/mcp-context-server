from __future__ import annotations

import logging
import time

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
)

logger = logging.getLogger(__name__)

# $0.14 por 1M tokens input, $0.28 por 1M tokens output
COST_INPUT_PER_TOKEN = 0.14 / 1_000_000
COST_OUTPUT_PER_TOKEN = 0.28 / 1_000_000

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


def _call(prompt: str, chunks: list[dict]) -> tuple[str, int, int, float]:
    """Llamada a DeepSeek con reintentos y fallback a chunks crudos. Compartida
    por compress_context y audit_context."""
    client = _get_client()
    last_error: Exception | None = None
    for attempt in range(DEEPSEEK_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=DEEPSEEK_MODEL,
                max_tokens=DEEPSEEK_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens * COST_INPUT_PER_TOKEN) + (output_tokens * COST_OUTPUT_PER_TOKEN)
            logger.debug("DeepSeek: %d in / %d out tokens, $%.6f", input_tokens, output_tokens, cost)
            return content, input_tokens, output_tokens, cost
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
        "DeepSeek no disponible tras %d intentos (%s). Devolviendo chunks crudos.",
        DEEPSEEK_MAX_RETRIES + 1, last_error,
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


def audit_context(instructions: str, chunks: list[dict]) -> tuple[str, int, int, float]:
    """Variante de compress_context para auditorías: pide hallazgos estructurados
    con severidad, archivo:línea y fix. Retorna (findings, in_tok, out_tok, costo).

    Parte los chunks en lotes que quepan en la ventana del modelo (ver _batch_by_chars)
    y concatena los hallazgos. Una categoría con muchos archivos se audita en varias
    pasadas en vez de fallar con error 400."""
    if not chunks:
        return "Sin hallazgos.", 0, 0, 0.0

    batches = _batch_by_chars(chunks, AUDIT_BATCH_MAX_CHARS)
    findings: list[str] = []
    total_in = total_out = 0
    total_cost = 0.0
    for batch in batches:
        prompt = (
            f"{AUDIT_SYSTEM_INSTRUCTIONS}\n\n"
            f"TAREA DE AUDITORÍA: {instructions}\n\n"
            f"Fragmentos de código a auditar:\n\n{_build_fragments(batch)}"
        )
        text, in_tok, out_tok, cost = _call(prompt, batch)
        if text and text.strip() and text.strip() != "Sin hallazgos.":
            findings.append(text.strip())
        total_in += in_tok
        total_out += out_tok
        total_cost += cost

    if not findings:
        return "Sin hallazgos.", total_in, total_out, total_cost
    return "\n".join(findings), total_in, total_out, total_cost


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
