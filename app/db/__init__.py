"""Postgres connection pool.

Every connection is registered with pgvector on checkout, so queries can pass
and receive `Vector` values directly.
"""

from contextlib import contextmanager
import logging

from pgvector.psycopg import register_vector
from psycopg import Error as DatabaseError, OperationalError
from psycopg_pool import ConnectionPool, PoolTimeout

from app.config import get_settings


_pool = None
_opened = False
logger = logging.getLogger(__name__)


def _configure(conn):
    """Runs once per new connection in the pool."""

    register_vector(conn)


def _check_connection(conn):
    """Reject a connection that died while it was idle in the pool."""

    try:
        ConnectionPool.check_connection(conn)
    except DatabaseError:
        logger.warning("stale connection detected during pool checkout")
        raise


def _reconnect_failed(pool):
    """Called after the pool cannot recreate a connection within its budget."""

    logger.error(
        "database unavailable: pool %s exhausted its reconnection deadline",
        pool.name,
    )


def get_pool():
    """Lazily build the pool. Safe to call from anywhere."""

    global _pool

    if _pool is None:

        settings = get_settings()

        _pool = ConnectionPool(
            conninfo=settings.require_database_url(),
            # Never permit an accidental non-TLS deployment URL to downgrade
            # database traffic. Keyword arguments override conninfo values.
            kwargs={"sslmode": "require"},
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            configure=_configure,
            check=_check_connection,
            timeout=settings.db_pool_timeout_seconds,
            max_lifetime=settings.db_pool_max_lifetime_seconds,
            max_idle=settings.db_pool_max_idle_seconds,
            reconnect_timeout=settings.db_pool_reconnect_timeout_seconds,
            reconnect_failed=_reconnect_failed,
            name="application-db",
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


def run_read(operation, *, operation_name="database read"):
    """Run an idempotent read, replacing one connection dropped in flight.

    The failed exception is allowed to leave the pool's connection context;
    psycopg-pool can therefore discard the broken connection normally.  Only
    explicitly opted-in reads use this helper: writes must decide retry safety
    using their own transaction/idempotency semantics.
    """

    for attempt in range(2):
        try:
            with connection() as conn:
                return operation(conn)
        except PoolTimeout:
            logger.error(
                "pool exhaustion while waiting to run %s", operation_name
            )
            raise
        except OperationalError:
            if attempt == 0:
                logger.warning(
                    "stale connection dropped during %s; retrying once",
                    operation_name,
                    exc_info=True,
                )
                continue

            logger.error(
                "database unavailable during %s after one retry",
                operation_name,
                exc_info=True,
            )
            raise
        except DatabaseError:
            logger.error("query failure during %s", operation_name, exc_info=True)
            raise

    raise AssertionError("unreachable")
