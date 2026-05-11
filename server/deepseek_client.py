from __future__ import annotations

import logging
import anthropic

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

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
        )
    return _client


def compress_context(query: str, chunks: list[dict]) -> tuple[str, int, int, float]:
    """
    Envia los chunks a DeepSeek Flash para que filtre y comprima segun la query.
    Retorna: (contexto_comprimido, input_tokens, output_tokens, costo_usd)
    """
    if not chunks:
        return "", 0, 0, 0.0

    fragments = "\n\n---\n\n".join(
        f"# {c['file_path']} (chunk {c['chunk_index']})\n{c['content']}"
        for c in chunks
    )

    prompt = (
        f"El desarrollador pregunta: {query}\n\n"
        f"A continuacion hay fragmentos de codigo de su proyecto. "
        f"Extrae y resume SOLO lo relevante para responder su pregunta. "
        f"Si un fragmento no aporta nada, ignoralo. "
        f"Usa el mismo idioma que la pregunta. Se conciso.\n\n"
        f"{fragments}"
    )

    client = _get_client()
    response = client.messages.create(
        model=DEEPSEEK_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    content = response.content[0].text
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    cost = (input_tokens * COST_INPUT_PER_TOKEN) + (output_tokens * COST_OUTPUT_PER_TOKEN)

    logger.debug(
        "DeepSeek: %d in / %d out tokens, $%.6f",
        input_tokens, output_tokens, cost,
    )

    return content, input_tokens, output_tokens, cost
