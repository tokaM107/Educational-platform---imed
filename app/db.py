"""Postgres connection pool.

Every connection is registered with pgvector on checkout, so queries can pass
and receive `Vector` values directly.
"""

from contextlib import contextmanager

from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from app.config import get_settings


_pool = None
_opened = False


def _configure(conn):
    """Runs once per new connection in the pool."""

    register_vector(conn)


def get_pool():
    """Lazily build the pool. Safe to call from anywhere."""

    global _pool

    if _pool is None:

        settings = get_settings()

        _pool = ConnectionPool(
            conninfo=settings.require_database_url(),
            min_size=1,
            max_size=10,
            configure=_configure,
            open=False,
        )

    return _pool


def open_pool():
    """Idempotent: the API opens it on startup, the CLI scripts on first use."""

    global _opened

    if not _opened:
        get_pool().open(wait=True, timeout=10)
        _opened = True


def close_pool():

    global _pool, _opened

    if _pool is not None:
        _pool.close()
        _pool = None
        _opened = False


@contextmanager
def connection():
    """`with connection() as conn:` — returns the connection to the pool after."""

    open_pool()

    with get_pool().connection() as conn:
        yield conn
