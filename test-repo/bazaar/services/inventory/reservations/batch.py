"""Batch reservation path for POST /v1/reserve/batch.

Handles up to RESERVE_BATCH_MAX items per call. The whole batch locks,
decrements, and inserts through a fixed number of statements, each taking
the SKU set as one array parameter.
"""

from __future__ import annotations

import logging

from libs.dbkit.pool import get_pool

from services.inventory.allocator import choose_bin
from services.inventory.errors import OutOfStockError, UnknownSkuError
from services.inventory.models import ReserveItem
from services.inventory.reservations.expiry import new_expiry
from services.inventory.reservations.models import Reservation, ReservationStatus

log = logging.getLogger("inventory.reservations")

# ORDER BY keeps lock acquisition aligned with the single-item path.
_LOCK_BATCH_SQL = """
    SELECT sku, available
      FROM stock
     WHERE sku = ANY($1)
     ORDER BY sku
       FOR UPDATE
"""

_DECREMENT_BATCH_SQL = """
    UPDATE stock AS s
       SET available = s.available - d.quantity,
           reserved = s.reserved + d.quantity
      FROM unnest($1::text[], $2::int[]) AS d(sku, quantity)
     WHERE s.sku = d.sku
"""

_INSERT_BATCH_SQL = """
    INSERT INTO reservations (order_id, sku, quantity, bin_label, status, expires_at)
    SELECT $1, r.sku, r.quantity, r.bin_label, $2, $3
      FROM unnest($4::text[], $5::int[], $6::text[]) AS r(sku, quantity, bin_label)
    RETURNING id, sku
"""


async def create_batch(order_id: str, zone: str, items: list[ReserveItem]) -> list[Reservation]:
    """Reserve every item in one transaction; all lines succeed or none do."""
    ordered = sorted(items, key=lambda item: item.sku)
    skus = [item.sku for item in ordered]
    quantities = [item.quantity for item in ordered]
    bins = [choose_bin(sku, zone) for sku in skus]
    expires_at = new_expiry()

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(_LOCK_BATCH_SQL, skus)
            available = {row["sku"]: row["available"] for row in rows}
            for item in ordered:
                held = available.get(item.sku)
                if held is None:
                    raise UnknownSkuError(item.sku)
                if held < item.quantity:
                    raise OutOfStockError(item.sku, item.quantity, held)
            await conn.execute(_DECREMENT_BATCH_SQL, skus, quantities)
            inserted = await conn.fetch(
                _INSERT_BATCH_SQL,
                order_id,
                ReservationStatus.ACTIVE.value,
                expires_at,
                skus,
                quantities,
                bins,
            )

    quantity_by_sku = dict(zip(skus, quantities))
    bin_by_sku = dict(zip(skus, bins))
    log.info("reserved %d skus for order %s", len(ordered), order_id)
    return [
        Reservation(
            id=row["id"],
            order_id=order_id,
            sku=row["sku"],
            quantity=quantity_by_sku[row["sku"]],
            bin_label=bin_by_sku[row["sku"]],
            status=ReservationStatus.ACTIVE,
            expires_at=expires_at,
        )
        for row in inserted
    ]
