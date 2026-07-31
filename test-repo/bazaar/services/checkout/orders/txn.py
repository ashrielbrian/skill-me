"""Order persistence: one atomic write per order, plus status moves."""

from libs.dbkit.executor import execute
from libs.dbkit.pool import get_pool

_INSERT_ORDER = """
    INSERT INTO orders (id, buyer_id, status, currency, total_cents, items_json,
                        created_at, updated_at)
    VALUES ($1, $2, $3, $4, $5, $6, now(), now())
"""
_INSERT_ITEM = """
    INSERT INTO order_items (order_id, product_id, category, quantity,
                             unit_price_cents, tax_rate)
    VALUES ($1, $2, $3, $4, $5, $6)
"""
_UPDATE_STATUS = "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1"


async def update_status(order_id, status):
    """Moves one order to a new status. Called on cancels, a few times
    per minute."""
    await execute(_UPDATE_STATUS, (order_id, status))


async def write_order(order, items):
    """Persists one order header and its line items atomically.

    Called once per order from orders/create.py. The header and every
    line land in a single transaction so a failure leaves no partial
    order behind.
    """
    pool = get_pool()
    rows = [
        (
            order.id,
            item.product_id,
            item.category,
            item.quantity,
            item.unit_price_cents,
            item.tax_rate,
        )
        for item in items
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                _INSERT_ORDER,
                order.id,
                order.buyer_id,
                order.status,
                order.currency,
                order.total_cents,
                order.items_json,
            )
            await conn.executemany(_INSERT_ITEM, rows)
