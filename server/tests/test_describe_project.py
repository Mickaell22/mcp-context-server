"""describe_project: los datos duros se miden, no se le preguntan al modelo.

Offline: solo ejercita los extractores deterministas (paleta, stack, estructura,
nombres). La sintesis DeepSeek no se testea.
"""

from __future__ import annotations

from tools import describe_project as dp


def _chunk(file_path: str, content: str) -> dict:
    return {"file_path": file_path, "chunk_index": 0, "content": content}


# ---------- paleta ----------

CSS = """
:root {
  --primary: #2563EB;
  --danger: #dc2626;
  --surface: rgb(248, 250, 252);
}
.btn { background: #2563eb; color: #fff; }
.btn:hover { background: #2563eb; }
.alert { border: 1px solid #dc2626; }
"""


def test_paleta_cuenta_usos_y_ordena_por_frecuencia():
    palette = dp.extract_palette([_chunk("src/app.css", CSS)])
    colores = [p["color"] for p in palette]
    assert colores[0] == "#2563eb", f"el color mas usado deberia ir primero: {palette}"
    assert palette[0]["uses"] == 3  # la declaracion de la var + los dos usos


def test_paleta_normaliza_mayusculas():
    # #2563EB en la variable y #2563eb en el uso son el MISMO color
    palette = dp.extract_palette([_chunk("a.css", CSS)])
    assert len([p for p in palette if p["color"].lower() == "#2563eb"]) == 1


def test_paleta_asocia_el_token_que_nombra_al_color():
    palette = {p["color"]: p for p in dp.extract_palette([_chunk("a.css", CSS)])}
    assert palette["#2563eb"]["tokens"] == ["--primary"]
    assert palette["#dc2626"]["tokens"] == ["--danger"]


def test_paleta_ignora_blanco_y_negro():
    # #fff aparece en el CSS pero no dice nada de la identidad visual
    assert all(p["color"] not in ("#fff", "#ffffff") for p in dp.extract_palette([_chunk("a.css", CSS)]))


def test_paleta_captura_funciones_de_color():
    palette = {p["color"] for p in dp.extract_palette([_chunk("a.css", CSS)])}
    assert "rgb(248, 250, 252)" in palette


def test_archivo_sin_colores_no_aporta_ruido():
    assert dp.extract_palette([_chunk("a.py", "def f():\n    return 1\n")]) == []


def test_paleta_vacia_sin_chunks():
    assert dp.extract_palette([]) == []


# ---------- stack ----------

def test_parse_package_json_separa_runtime_de_dev():
    content = '{"dependencies": {"react": "^18"}, "devDependencies": {"vite": "^5"}, "scripts": {"dev": "vite"}}'
    parsed = dp._parse_manifest("package.json", content)
    assert parsed["runtime"] == ["react"]
    assert parsed["dev"] == ["vite"]
    assert parsed["scripts"] == ["dev"]


def test_package_json_roto_no_revienta():
    assert dp._parse_manifest("package.json", "{no es json") is None


def test_parse_requirements_limpia_versiones_y_comentarios():
    content = "fastapi==0.110.0\n# comentario\n-r otro.txt\nchromadb>=1.0\npsycopg2-binary\n\n"
    parsed = dp._parse_manifest("requirements.txt", content)
    assert parsed["runtime"] == ["chromadb", "fastapi", "psycopg2-binary"]


def test_manifiesto_desconocido_se_deja_crudo():
    assert dp._parse_manifest("pom.xml", "<project/>") is None


def test_el_header_del_chunker_no_rompe_el_manifiesto():
    # el chunker antepone "// ruta": rompia json.loads y se colaba como una
    # dependencia llamada "//"
    parsed = dp._parse_manifest("package.json", '// app/package.json\n{"dependencies": {"vue": "^3"}}')
    assert parsed["runtime"] == ["vue"]

    parsed = dp._parse_manifest("requirements.txt", "// server/requirements.txt\nfastapi==1.0\n")
    assert parsed["runtime"] == ["fastapi"]


def test_el_muestreo_descarta_tests_y_fixtures():
    # los mocks imitan OTRO sistema a proposito: no son las convenciones del repo
    chunks = [
        _chunk("server/tools/query.py", "x"),
        _chunk("server/tests/fixtures/fake_backend/routers/auth.py", "x"),
        _chunk("src/__mocks__/api.ts", "x"),
        _chunk("e2e/login.spec.ts", "x"),
    ]
    assert [c["file_path"] for c in dp._without_tests(chunks)] == ["server/tools/query.py"]


# ---------- estructura y nombres ----------

def test_estructura_cuenta_archivos_por_carpeta():
    files = ["src/components/Btn.tsx", "src/components/Card.tsx", "src/lib/api.ts", "main.py"]
    dirs = {d["dir"]: d["files"] for d in dp._structure(files)}
    assert dirs["src/components"] == 2
    assert dirs["src/lib"] == 1
    assert "" not in dirs  # un archivo en la raiz no crea una carpeta vacia


def test_naming_detecta_la_convencion_dominante():
    files = ["src/UserCard.tsx", "src/OrderList.tsx", "src/api_client.py"]
    assert dp._naming(files)["dominant"] == "PascalCase"

    files = ["src/user-card.ts", "src/order-list.ts", "src/Main.ts"]
    assert dp._naming(files)["dominant"] == "kebab-case"
