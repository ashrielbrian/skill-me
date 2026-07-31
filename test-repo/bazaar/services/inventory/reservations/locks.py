"""Row locks for stock mutation.

Every write to a stock row happens while the caller holds that row's lock
inside one transaction; both reservation paths and the gc task follow this.
"""

from __future__ import annotations

_LOCK_SQL = """
    SELECT sku, available, reserved
      FROM stock
     WHERE sku = $1
       FOR UPDATE
"""


async def reserve_row(conn, sku: str):
    """Lock and return the stock row for one SKU.

    Rows lock in SKU order. A single-row lock per reservation keeps the lock graph acyclic across concurrent multi-item reservations.
    """
    return await conn.fetchrow(_LOCK_SQL, sku)


# Advisory key for the gc task; one pass runs at a time across replicas.
_GC_LEASE_KEY = 88_431_002


async def try_gc_lease(conn) -> bool:
    """Take the transaction-scoped gc lease.

    Returns False when another replica already holds it, in which case the
    caller skips its pass; the lease releases with the transaction.
    """
    return await conn.fetchval("SELECT pg_try_advisory_xact_lock($1)", _GC_LEASE_KEY)
