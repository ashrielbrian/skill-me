"""Removal of expired reservations.

Runs from the periodic task started in app.py, every GC_INTERVAL_S seconds.
Each pass streams expired row ids through a server-side cursor and deletes
them in GC_BATCH_ROWS chunks, returning held stock as it goes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from libs.dbkit.pool import get_pool

from services.inventory.config import GC_BATCH_ROWS, GC_INTERVAL_S
from services.inventory.reservations.locks import try_gc_lease

log = logging.getLogger("inventory.gc")

_EXPIRED_SQL = """
    SELECT id
      FROM reservations
     WHERE status = 'active'
       AND expires_at < $1
     ORDER BY id
"""

# Returns held quantities to stock and removes the rows in one statement.
_DELETE_SQL = """
    WITH gone AS (
        DELETE FROM reservations
         WHERE id = ANY($1)
        RETURNING sku, quantity
    )
    UPDATE stock AS s
       SET available = s.available + g.quantity,
           reserved = s.reserved - g.quantity
      FROM (SELECT sku, sum(quantity) AS quantity FROM gone GROUP BY sku) AS g
     WHERE s.sku = g.sku
"""


async def collect_expired(now: datetime | None = None) -> int:
    """Delete expired reservations; returns rows removed in this pass."""
    cutoff = now if now is not None else datetime.now(timezone.utc)
    pool = get_pool()
    removed = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await try_gc_lease(conn):
                return 0
            batch: list[int] = []
            async for record in conn.cursor(_EXPIRED_SQL, cutoff):
                batch.append(record["id"])
                if len(batch) == GC_BATCH_ROWS:
                    await conn.execute(_DELETE_SQL, batch)
                    removed += len(batch)
                    batch.clear()
            if batch:
                await conn.execute(_DELETE_SQL, batch)
                removed += len(batch)
    if removed:
        log.info("removed %d expired reservations", removed)
    return removed


async def run_forever() -> None:
    """Periodic loop; app.py schedules this as a background task."""
    while True:
        try:
            await collect_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("gc pass failed; next pass in %d s", GC_INTERVAL_S)
        await asyncio.sleep(GC_INTERVAL_S)
