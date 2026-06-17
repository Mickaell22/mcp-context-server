from __future__ import annotations

import threading
from abc import ABC, abstractmethod

# ---------------------------------------------------------------------------
# SOBRE-INGENIERIA SEMBRADA (la categoria over-engineering DEBE detectar esto)
# ---------------------------------------------------------------------------


class BaseNotifier(ABC):
    """Interfaz de notificador con una sola implementacion concreta en todo el
    repo: abstraccion prematura, se puede colapsar en EmailNotifier."""

    @abstractmethod
    def send(self, to: str, msg: str) -> None:
        ...


class EmailNotifier(BaseNotifier):
    def send(self, to: str, msg: str) -> None:
        print(f"email a {to}: {msg}")


class NotifierFactory:
    """Factory que solo puede construir un producto: indireccion sin valor."""

    @staticmethod
    def create(kind: str = "email") -> BaseNotifier:
        return EmailNotifier()


class Message:
    """DTO espejo con getters/setters triviales (boilerplate)."""

    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text

    def set_text(self, value: str) -> None:
        self._text = value


# ---------------------------------------------------------------------------
# NO debe marcarse como over-engineering (guardarrail de la categoria)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_counter = {"n": 0}


def registrar_envio() -> int:
    # Concurrencia necesaria: el lock protege un contador compartido entre hilos.
    # NO es sobre-ingenieria; borrarlo introduce una race condition.
    with _lock:
        _counter["n"] += 1
        return _counter["n"]


def validar_destinatario(email: str) -> str:
    # Validacion de input en limite de confianza (viene de una API publica).
    # NO es defensa redundante; borrarla deja entrar datos invalidos.
    if not email or "@" not in email:
        raise ValueError("email invalido")
    return email.strip().lower()
