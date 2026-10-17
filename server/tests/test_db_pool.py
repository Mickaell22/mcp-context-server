"""Pool de conexiones de db.cursor(): reutiliza, descarta ociosas y caidas."""

import psycopg2
import pytest

import db


class FakeConn:
    opened = 0

    def __init__(self):
        FakeConn.opened += 1
        self.closed = 0

    def cursor(self, cursor_factory=None):
        conn = self

        class Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, q, *a):
                if q == "boom":
                    conn.closed = 2
                    raise psycopg2.OperationalError("server closed the connection")

        return Cur()

    def commit(self): pass
    def rollback(self): pass
    def close(self): self.closed = 1


@pytest.fixture(autouse=True)
def fake_pool(monkeypatch):
    FakeConn.opened = 0
    db._pool.clear()
    monkeypatch.setattr(db, "get_connection", FakeConn)
    yield
    db._pool.clear()


def test_reutiliza_la_conexion():
    for _ in range(3):
        with db.cursor() as cur:
            cur.execute("select 1")
    assert FakeConn.opened == 1


def test_descarta_la_conexion_caida():
    with pytest.raises(psycopg2.OperationalError):
        with db.cursor() as cur:
            cur.execute("boom")
    assert not db._pool
    with db.cursor() as cur:
        cur.execute("select 1")
    assert FakeConn.opened == 2


def test_descarta_la_conexion_ociosa():
    with db.cursor() as cur:
        cur.execute("select 1")
    conn, last = db._pool[0]
    db._pool[0] = (conn, last - db._POOL_MAX_IDLE - 1)
    with db.cursor() as cur:
        cur.execute("select 1")
    assert FakeConn.opened == 2 and conn.closed
