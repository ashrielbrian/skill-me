"""Cart storage. All access goes through dbkit with bound parameters."""

from libs.dbkit.executor import execute, fetch_all, fetch_one
from services.checkout.errors import CartNotFound

_CART_QUERY = "SELECT id, buyer_id, status, updated_at FROM carts WHERE id = $1"
_ITEMS_QUERY = """
    SELECT product_id, category, quantity
    FROM cart_items
    WHERE cart_id = $1
    ORDER BY added_at
"""
_UPSERT_ITEM = """
    INSERT INTO cart_items (cart_id, product_id, category, quantity, added_at)
    VALUES ($1, $2, $3, $4, now())
    ON CONFLICT (cart_id, product_id)
    DO UPDATE SET quantity = EXCLUDED.quantity
"""
_DELETE_ITEM = "DELETE FROM cart_items WHERE cart_id = $1 AND product_id = $2"
_CLEAR_ITEMS = "DELETE FROM cart_items WHERE cart_id = $1"
_TOUCH_CART = "UPDATE carts SET updated_at = now() WHERE id = $1"


async def load_cart(cart_id):
    """Reads one cart header and its lines.

    Called on cart views and at order placement, a few dozen times per
    second. The item read runs only after the header is known to exist.
    """
    cart = await fetch_one(_CART_QUERY, (cart_id,))
    if cart is None:
        raise CartNotFound(cart_id)
    items = await fetch_all(_ITEMS_QUERY, (cart_id,))
    return {
        "cart_id": cart["id"],
        "buyer_id": cart["buyer_id"],
        "status": cart["status"],
        "items": [dict(row) for row in items],
    }


async def set_item(cart_id, product_id, category, quantity):
    """Adds or updates one cart line. Called once per cart edit."""
    await execute(_UPSERT_ITEM, (cart_id, product_id, category, quantity))
    await execute(_TOUCH_CART, (cart_id,))


async def remove_item(cart_id, product_id):
    """Deletes one cart line. Called once per removal."""
    await execute(_DELETE_ITEM, (cart_id, product_id))
    await execute(_TOUCH_CART, (cart_id,))


async def clear_cart(cart_id):
    """Empties one cart after its order places. Called once per order."""
    await execute(_CLEAR_ITEMS, (cart_id,))
    await execute(_TOUCH_CART, (cart_id,))
