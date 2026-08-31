"""Tests del chunking de indexer: _chunk_tsx, _chunk_css, _chunk_python y el
fallback por lineas, mas la extraccion de simbolos.

Solo se ejercitan funciones puras de troceo; no se cargan embeddings ni se toca
ChromaDB (el modelo y la coleccion se instancian de forma perezosa).

NOTA: desde el soporte de numeros de linea, las funciones de segmento devuelven
tuplas (offset_0based, lineas) y _chunk_content devuelve
(contenido, start_line_1based, end_line_1based).
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
    # cada segmento es (offset_0based, lineas)
    assert segments[0][0] == 0
    assert "import React" in "".join(segments[0][1])
    assert "Header" in "".join(segments[1][1])
    assert "Footer" in "".join(segments[2][1])
    # offsets crecientes
    assert segments[1][0] < segments[2][0]


def test_chunk_tsx_un_solo_boundary_retorna_none():
    src = (
        "import React from 'react'\n"
        "export function Solo() {\n"
        "  return null\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    assert indexer._chunk_tsx(lines) is None


# ---------- _chunk_js ----------

def test_chunk_js_separa_funciones_en_minuscula():
    """A diferencia de _chunk_tsx, no exige mayuscula -- caso real: un app.js sin framework
    con funciones sueltas top-level en minuscula (ej. Bitacora de Compras)."""
    src = (
        "let ultimoArchivo = null;\n"
        "\n"
        "function recalcularAhorro() {\n"
        "  return 1;\n"
        "}\n"
        "\n"
        "function cgImportar() {\n"
        "  return 2;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_js(lines)

    assert segments is not None
    assert len(segments) == 3
    assert "ultimoArchivo" in "".join(segments[0][1])
    assert "recalcularAhorro" in "".join(segments[1][1])
    assert "cgImportar" in "".join(segments[2][1])


def test_chunk_js_incluye_const_flecha_top_level():
    src = (
        "const foo = (x) => {\n"
        "  return x;\n"
        "}\n"
        "\n"
        "async function bar() {\n"
        "  return 1;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_js(lines)

    assert segments is not None
    assert len(segments) == 2
    assert "const foo" in "".join(segments[0][1])
    assert "async function bar" in "".join(segments[1][1])


def test_chunk_js_const_simple_no_es_boundary():
    """Una constante con valor literal (no funcion) no debe generar un boundary propio --
    solo importa donde empiezan las funciones/clases."""
    src = (
        "const LOTE = 50;\n"
        "let x = null;\n"
        "\n"
        "function unica() {\n"
        "  return 1;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    # una sola funcion real -> <2 boundaries -> None (fallback por lineas)
    assert indexer._chunk_js(lines) is None


def test_chunk_js_un_solo_boundary_retorna_none():
    src = "function sola() {\n  return 1;\n}\n"
    lines = src.splitlines(keepends=True)
    assert indexer._chunk_js(lines) is None


def test_chunk_js_nested_no_es_boundary():
    """Una funcion/const anidada (indentada) no cuenta como boundary top-level."""
    src = (
        "function externa() {\n"
        "  const interna = () => 1;\n"
        "  return interna();\n"
        "}\n"
        "\n"
        "function otra() {\n"
        "  return 2;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_js(lines)

    assert segments is not None
    assert len(segments) == 2
    # 'interna' quedo DENTRO del chunk de 'externa', no genero un tercer segmento
    assert "interna" in "".join(segments[0][1])


# ---------- _chunk_php ----------

def test_chunk_php_separa_funciones():
    src = (
        "<?php\n"
        "declare(strict_types=1);\n"
        "\n"
        "function db(): mysqli\n"
        "{\n"
        "    return null;\n"
        "}\n"
        "\n"
        "function bitacora_guardar(array $d): int\n"
        "{\n"
        "    return 1;\n"
        "}\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_php(lines)

    assert segments is not None
    assert len(segments) == 3
    assert "declare(strict_types" in "".join(segments[0][1])
    assert "function db" in "".join(segments[1][1])
    assert "function bitacora_guardar" in "".join(segments[2][1])


def test_chunk_php_un_solo_boundary_retorna_none():
    src = "<?php\nfunction sola() {\n    return 1;\n}\n"
    lines = src.splitlines(keepends=True)
    assert indexer._chunk_php(lines) is None


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
    assert ":root" in "".join(segments[0][1])
    assert ".dark" in "".join(segments[1][1])
    # el segundo bloque empieza despues del primero
    assert segments[1][0] >= 3


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
    assert "@media" in "".join(segments[0][1])
    assert ".otro" in "".join(segments[1][1])


def test_chunk_css_un_solo_bloque_retorna_none():
    src = ".solo {\n  color: red;\n}\n"
    lines = src.splitlines(keepends=True)
    assert indexer._chunk_css(lines) is None


# ---------- _chunk_python ----------

def test_chunk_python_separa_defs_y_clases():
    src = (
        "import os\n"
        "X = 1\n"
        "\n"
        "@router.put('/{id}')\n"
        "def actualizar(id):\n"
        "    return id\n"
        "\n"
        "class Foo:\n"
        "    def bar(self):\n"
        "        return 2\n"
    )
    lines = src.splitlines(keepends=True)
    segments = indexer._chunk_python(src, lines)

    assert segments is not None
    # preludio (import + constante) + funcion + clase
    assert len(segments) == 3
    assert "import os" in "".join(segments[0][1]) and "X = 1" in "".join(segments[0][1])
    # el decorador debe ir CON la funcion (no en el preludio)
    func_text = "".join(segments[1][1])
    assert "@router.put" in func_text and "def actualizar" in func_text
    assert "class Foo" in "".join(segments[2][1])


def test_chunk_python_pocas_defs_retorna_none():
    # una sola definicion top-level -> None -> fallback por lineas
    assert indexer._chunk_python("def solo():\n    pass\n", ["def solo():\n", "    pass\n"]) is None


def test_chunk_python_invalido_retorna_none():
    assert indexer._chunk_python("esto no es python {", ["esto no es python {"]) is None


# ---------- _extract_symbols ----------

def test_extract_symbols_python():
    content = "def actualizar_pedido(id):\n    pass\nclass Pedido:\n    pass\n"
    syms = indexer._extract_symbols(content, ".py")
    assert "actualizar_pedido" in syms and "Pedido" in syms


def test_extract_symbols_js():
    content = "export function Dashboard() {}\nconst getPedidos = async () => {}\nclass Foo {}\n"
    syms = indexer._extract_symbols(content, ".jsx")
    assert "Dashboard" in syms and "getPedidos" in syms and "Foo" in syms


def test_extract_symbols_no_code_ext():
    assert indexer._extract_symbols("# titulo\n", ".md") == ""


# ---------- _chunk_content (devuelve tuplas content, start_line, end_line) ----------

def test_chunk_content_fallback_por_lineas():
    total = CHUNK_SIZE * 2  # fuerza mas de un chunk
    content = "".join(f"line {i}\n" for i in range(total))
    chunks = indexer._chunk_content(content, "modulo.py")

    assert len(chunks) > 1
    # cada chunk es (contenido, start_line, end_line) y lleva el header con la ruta
    assert all(c[0].startswith("// modulo.py\n") for c in chunks)
    # el primer chunk empieza en la linea 1
    assert chunks[0][1] == 1


def test_chunk_content_fallback_overlap():
    total = CHUNK_SIZE + 10
    content = "".join(f"line {i}\n" for i in range(total))
    chunks = indexer._chunk_content(content, "modulo.py")

    assert len(chunks) == 2
    contenido_segundo, start_line_segundo, end_line_segundo = chunks[1]
    # el avance entre chunks es CHUNK_SIZE - CHUNK_OVERLAP
    assert f"line {CHUNK_SIZE - CHUNK_OVERLAP}\n" in contenido_segundo
    # start_line es 1-based: la primera linea del segundo chunk
    assert start_line_segundo == (CHUNK_SIZE - CHUNK_OVERLAP) + 1
    # end_line 1-based: la ultima linea del archivo (total lineas)
    assert end_line_segundo == CHUNK_SIZE + 10


def test_chunk_content_python_start_line_real():
    """El start_line de cada chunk debe ser la linea real de la def en el archivo."""
    src = (
        "import os\n"          # linea 1
        "\n"                    # linea 2
        "def primera():\n"      # linea 3
        "    return 1\n"        # linea 4
        "\n"                    # linea 5
        "def segunda():\n"      # linea 6
        "    return 2\n"        # linea 7
    )
    chunks = indexer._chunk_content(src, "m.py")
    # preludio(1) + primera(3) + segunda(6)
    start_lines = [sl for _, sl, _ in chunks]
    assert start_lines == [1, 3, 6]
    # rangos completos: preludio 1-2, primera 3-5, segunda 6-7
    rangos = [(sl, el) for _, sl, el in chunks]
    assert rangos == [(1, 2), (3, 5), (6, 7)]


def test_chunk_content_archivo_corto_un_chunk():
    content = "print('hola')\n"
    chunks = indexer._chunk_content(content, "mini.py")
    assert len(chunks) == 1
    assert "print('hola')" in chunks[0][0]
    assert chunks[0][1] == 1


def test_chunk_content_despacha_js_por_extension():
    src = (
        "function primera() {\n"
        "  return 1;\n"
        "}\n"
        "\n"
        "function segunda() {\n"
        "  return 2;\n"
        "}\n"
    )
    chunks = indexer._chunk_content(src, "app.js")
    assert len(chunks) == 2
    assert "function primera" in chunks[0][0]
    assert "function segunda" in chunks[1][0]


def test_chunk_content_despacha_php_por_extension():
    src = (
        "<?php\n"
        "function primera() {\n"
        "    return 1;\n"
        "}\n"
        "\n"
        "function segunda() {\n"
        "    return 2;\n"
        "}\n"
    )
    chunks = indexer._chunk_content(src, "datos.php")
    # preludio ("<?php") + funcion primera + funcion segunda
    assert len(chunks) == 3
    assert "function primera" in chunks[1][0]
    assert "function segunda" in chunks[2][0]


def test_chunk_content_sin_rel_path_no_header():
    content = "a\nb\nc\n"
    chunks = indexer._chunk_content(content)
    assert len(chunks) == 1
    assert not chunks[0][0].startswith("// ")
