import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

_conn: sqlite3.Connection | None = None


def connect(path: str | None = None) -> sqlite3.Connection:
    global _conn
    # isolation_level=None: the module controls transactions itself, see transaction().
    _conn = sqlite3.connect(path or settings.db_path, isolation_level=None, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript((Path(__file__).parent / "schema.sql").read_text())
    return _conn


def conn() -> sqlite3.Connection:
    return _conn if _conn is not None else connect()


def query(sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    row = conn().execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def execute(sql: str, params: tuple = ()) -> int:
    return conn().execute(sql, params).lastrowid


@contextmanager
def transaction():
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        c.execute("ROLLBACK")
        raise
    else:
        c.execute("COMMIT")
