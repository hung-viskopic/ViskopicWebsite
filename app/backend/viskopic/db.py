"""Database access.

Every query that touches tenant data runs inside `tenant_tx`, which sets the
`app.institution_id` setting for that transaction only. Row-level security policies
read that setting, so a query can never see another institution's rows even if the
application code forgets a WHERE clause.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tenant_tx(institution_id: UUID | str) -> Iterator[Connection]:
    """A transaction scoped to one institution."""
    with get_pool().connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.institution_id', %s, true)", (str(institution_id),))
        yield conn


@contextmanager
def system_tx() -> Iterator[Connection]:
    """A transaction with no tenant context: tenant tables return no rows.

    Used for login resolution, platform operator functions and job claiming.
    """
    with get_pool().connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.institution_id', '', true)")
        yield conn
