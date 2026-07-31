"""Async Postgres helpers shared by every bazaar service.

The package wraps asyncpg with the small set of conventions the
services agreed on:

- one connection pool per process (``pool.get_pool``),
- dict-shaped rows from the executor helpers,
- numbered placeholders rendered from named templates
  (``prepare.render_sql``),
- multi-row inserts grouped into flushes (``batch.insert_many``),
- retry classification for transient server errors (``retry``).

Import from this package root for the common entry points; the
individual modules stay importable for the less common helpers
(health probes, LISTEN/NOTIFY, keyset pagination, counters).
"""

from .batch import build_values_clause, insert_many
from .errors import DbkitError, PoolClosedError, QueryError, TransientError
from .executor import execute, fetch_all, fetch_one
from .pool import close_pool, get_pool
from .prepare import render_sql
from .txn import transaction

__all__ = [
    "DbkitError",
    "PoolClosedError",
    "QueryError",
    "TransientError",
    "build_values_clause",
    "close_pool",
    "execute",
    "fetch_all",
    "fetch_one",
    "get_pool",
    "insert_many",
    "render_sql",
    "transaction",
]
