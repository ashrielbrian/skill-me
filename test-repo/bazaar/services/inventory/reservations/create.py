"""Single-item reservation path for POST /v1/reserve.

The service takes ~2,000 reservations/min; each call runs one transaction:
lock the stock row, insert the reservation, move quantity to reserved.
"""

from __future__ import annotations

import logging

from libs.dbkit.pool import get_pool

from services.inventory.allocator import choose_bin
from services.inventory.errors import OutOfStockError, UnknownSkuError
from services.inventory.models import ReserveRequest
from services.inventory.reservations.expiry import new_expiry
from services.inventory.reservations.locks import reserve_row
from services.inventory.reservations.models import Reservation, ReservationStatus

log = logging.getLogger("inventory.reservations")

_INSERT_SQL = """
    INSERT INTO reservations (order_id, sku, quantity, bin_label, status, expires_at)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING id
"""

_DECREMENT_SQL = """
    UPDATE stock
       SET available = available - $2,
           reserved = reserved + $2
     WHERE sku = $1
"""


async def create_reservation(request: ReserveRequest) -> Reservation:
    """Reserve stock for one SKU inside a single transaction."""
    item = request.item
    bin_label = choose_bin(item.sku, request.zone)
    expires_at = new_expiry()

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await reserve_row(conn, item.sku)
            if row is None:
                raise UnknownSkuError(item.sku)
            if row["available"] < item.quantity:
                raise OutOfStockError(item.sku, item.quantity, row["available"])
            reservation_id = await conn.fetchval(
                _INSERT_SQL,
                request.order_id,
                item.sku,
                item.quantity,
                bin_label,
                ReservationStatus.ACTIVE.value,
                expires_at,
            )
            await conn.execute(_DECREMENT_SQL, item.sku, item.quantity)

    log.info("reserved %d of %s for order %s", item.quantity, item.sku, request.order_id)
    return Reservation(
        id=reservation_id,
        order_id=request.order_id,
        sku=item.sku,
        quantity=item.quantity,
        bin_label=bin_label,
        status=ReservationStatus.ACTIVE,
        expires_at=expires_at,
    )
