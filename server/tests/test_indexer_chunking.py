"""Tests del chunking de indexer: _chunk_tsx, _chunk_css y el fallback por lineas.

Solo se ejercitan funciones puras de troceo; no se cargan embeddings ni se toca
ChromaDB (el modelo y la coleccion se instancian de forma perezosa).
"""

import indexer
from config import CHUNK_SIZE, CHUNK_OVERLAP


# ---------- _chunk_tsx ----------

def test_chunk_tsx_separa_componentes():
    src = (
        "import React from 'react'\n"
        "\n"
        "export function Header() {\n"
        "  return <h1>hi</h1>\n"
        "}\n"
        "\n"
        "export function Footer() {\n"
        "  return <footer>bye</footer>\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_tsx(lines)

    assert segments is not None
    # preludio (imports) + 2 componentes
    assert len(segments) == 3
    assert "import React" in "".join(segments[0])
    assert "Header" in "".join(segments[1])
    assert "Footer" in "".join(segments[2])


def test_chunk_tsx_un_solo_boundary_retorna_none():
    src = (
        "import React from 'react'\n"
        "export function Solo() {\n"
        "  return null\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    assert indexer._chunk_tsx(lines) is None


# ---------- _chunk_css ----------

def test_chunk_css_separa_bloques():
    src = (
        ":root {\n"
        "  --bg: white;\n"
        "}\n"
        ".dark {\n"
        "  --bg: black;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_css(lines)

    assert segments is not None
    assert len(segments) == 2
    assert ":root" in "".join(segments[0])
    assert ".dark" in "".join(segments[1])


def test_chunk_css_anidado_se_mantiene_junto():
    """Una media query con regla anidada cuenta como un solo bloque top-level."""
    src = (
        "@media (max-width: 600px) {\n"
        "  .box {\n"
        "    color: red;\n"
        "  }\n"
        "}\n"
        ".otro {\n"
        "  color: blue;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_css(lines)

    assert segments is not None
    assert len(segments) == 2
    assert "@media" in "".join(segments[0])
    assert ".otro" in "".join(segments[1])


def test_chunk_css_un_solo_bloque_retorna_none():
    src = ".solo {\n  color: red;\n}\n"
    lines = src.splitlines(keepends=True)
    assert indexer._chunk_css(lines) is None


# ---------- fallback por lineas (_chunk_content) ----------

def test_chunk_content_fallback_por_lineas():
    total = CHUNK_SIZE * 2  # fuerza mas de un chunk
    content = "".join(f"line {i}\n" for i in range(total))
    chunks = indexer._chunk_content(content, "modulo.py")

    assert len(chunks) > 1
    # cada chunk lleva el header con la ruta
    assert all(c.startswith("// modulo.py\n") for c in chunks)


def test_chunk_content_fallback_overlap():
    total = CHUNK_SIZE + 10
    content = "".join(f"line {i}\n" for i in range(total))
    chunks = indexer._chunk_content(content, "modulo.py")

    # con CHUNK_SIZE+10 lineas y solape, esperamos exactamente 2 chunks
    assert len(chunks) == 2
    # el avance entre chunks es CHUNK_SIZE - CHUNK_OVERLAP, por lo que la primera
    # linea del segundo chunk es 'line {CHUNK_SIZE - CHUNK_OVERLAP}'
    segundo = chunks[1]
    assert f"line {CHUNK_SIZE - CHUNK_OVERLAP}\n" in segundo


def test_chunk_content_archivo_corto_un_chunk():
    content = "print('hola')\n"
    chunks = indexer._chunk_content(content, "mini.py")
    assert len(chunks) == 1
    assert "print('hola')" in chunks[0]


def test_chunk_content_sin_rel_path_no_header():
    content = "a\nb\nc\n"
    chunks = indexer._chunk_content(content)
    assert len(chunks) == 1
    assert not chunks[0].startswith("// ")
