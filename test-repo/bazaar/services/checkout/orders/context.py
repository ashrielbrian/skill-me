"""Reads the rows an order needs before it can be priced."""

from libs.dbkit.executor import fetch_one
from services.checkout.errors import CartNotFound, ValidationFailure
from services.checkout.orders.models import OrderContext

_BUYER_QUERY = "SELECT id, region, email, status FROM buyers WHERE id = $1"
_CART_QUERY = "SELECT id, buyer_id, status, updated_at FROM carts WHERE id = $1"
_ADDRESS_QUERY = """
    SELECT buyer_id, line1, line2, city, region, postal_code, country
    FROM addresses
    WHERE buyer_id = $1 AND kind = 'shipping'
"""


async def load_order_context(buyer_id, cart_id):
    """Loads the buyer row, the cart row, and the shipping address for
    one order. Runs once per order placement.
    """
    buyer = await fetch_one(_BUYER_QUERY, (buyer_id,))
    cart = await fetch_one(_CART_QUERY, (cart_id,))
    address = await fetch_one(_ADDRESS_QUERY, (buyer_id,))

    if buyer is None:
        raise ValidationFailure(["buyer %s does not exist" % buyer_id])
    if cart is None:
        raise CartNotFound(cart_id)
    if cart["buyer_id"] != buyer_id:
        raise ValidationFailure(
            ["cart %s does not belong to buyer %s" % (cart_id, buyer_id)]
        )
    if address is None:
        raise ValidationFailure(["buyer %s has no shipping address" % buyer_id])
    return OrderContext(buyer=buyer, cart=cart, address=address)
