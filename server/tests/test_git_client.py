"""Regresion del bug de acumulacion de token en remote.origin.url.

El bug: `check_remote_status` hacia `origin.set_url(_inject_token(origin.url))`,
que (a) escribia el token en texto plano en `.git/config` del repo del usuario y
(b) al leer la URL ya tokenizada en la llamada siguiente le anteponia otro token,
hasta dejar `https://ghp_x@ghp_x@...@github.com/...` y romper el fetch con
"URL rejected: Bad hostname" — perdiendo la deteccion de drift sin avisar.

No toca red ni disco: solo la manipulacion de URLs y un doble del remoto.
"""

import git_client


class FakeOrigin:
    """Doble de `git.Remote`: guarda la URL como lo haria .git/config."""

    name = "origin"

    def __init__(self, url: str):
        self.url = url
        self.set_url_calls = 0

    def set_url(self, url: str):
        self.url = url
        self.set_url_calls += 1


def _with_token(monkeypatch, token="ghp_TESTTOKEN123"):
    monkeypatch.setattr(git_client, "GITHUB_TOKEN", token)
    return token


CLEAN = "https://github.com/Mickaell22/restoventas-app.git"


def test_inject_token_es_idempotente(monkeypatch):
    """El nucleo del bug: aplicarlo N veces deja exactamente un token."""
    token = _with_token(monkeypatch)
    url = CLEAN
    for _ in range(5):
        url = git_client._inject_token(url)
    assert url.count("@") == 1
    assert url == f"https://{token}@github.com/Mickaell22/restoventas-app.git"


def test_inject_token_sanea_una_url_ya_corrompida(monkeypatch):
    token = _with_token(monkeypatch)
    corrupta = "https://ghp_VIEJO@ghp_VIEJO@ghp_VIEJO@github.com/u/r.git"
    assert git_client._inject_token(corrupta) == f"https://{token}@github.com/u/r.git"


def test_inject_token_no_toca_ssh_ni_hosts_ajenos(monkeypatch):
    _with_token(monkeypatch)
    for url in (
        "git@github.com:Mickaell22/restoventas-app.git",
        "https://gitlab.com/u/r.git",
        # el chequeo viejo era `"github.com" in netloc`: este host lo pasaba
        # y se llevaba el token a un servidor ajeno.
        "https://github.com.atacante.net/u/r.git",
    ):
        assert git_client._inject_token(url) == url


def test_inject_token_sin_token_configurado(monkeypatch):
    monkeypatch.setattr(git_client, "GITHUB_TOKEN", "")
    assert git_client._inject_token(CLEAN) == CLEAN


def test_clean_persisted_url_borra_el_token_de_git_config(monkeypatch):
    _with_token(monkeypatch)
    origin = FakeOrigin("https://ghp_VIEJO@ghp_VIEJO@github.com/u/r.git")
    assert git_client._clean_persisted_url(origin) == "https://github.com/u/r.git"
    assert origin.url == "https://github.com/u/r.git"  # reparado en disco


def test_clean_persisted_url_respeta_userinfo_legitimo(monkeypatch):
    _with_token(monkeypatch)
    origin = FakeOrigin("https://mickaell@github.com/u/r.git")
    assert git_client._clean_persisted_url(origin) == origin.url
    assert origin.set_url_calls == 0


def test_clean_persisted_url_no_escribe_si_ya_esta_limpia(monkeypatch):
    _with_token(monkeypatch)
    origin = FakeOrigin(CLEAN)
    assert git_client._clean_persisted_url(origin) == CLEAN
    assert origin.set_url_calls == 0


def test_clean_persisted_url_sin_token_configurado(monkeypatch):
    """Con GITHUB_TOKEN vacio, `token in userinfo` seria siempre True: no debe
    borrar userinfo legitimo por un `"" in ...`."""
    monkeypatch.setattr(git_client, "GITHUB_TOKEN", "")
    origin = FakeOrigin("https://mickaell@github.com/u/r.git")
    assert git_client._clean_persisted_url(origin) == origin.url
    assert origin.set_url_calls == 0


def test_redact_oculta_el_token_en_errores(monkeypatch):
    token = _with_token(monkeypatch)
    msg = f"fatal: unable to access 'https://{token}@github.com/u/r.git': 403"
    redactado = git_client._redact(msg)
    assert token not in redactado
    assert "***" in redactado
