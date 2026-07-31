"""Stock reservation step for order placement.

Holds inventory for each line item before the order row is written.
Holds expire on their own after ten minutes if the order never lands.
"""

import httpx

from services.checkout.config import INVENTORY_BASE_URL, INVENTORY_TIMEOUT_S
from services.checkout.errors import ReservationFailure

# One client per process, shared by every order.
_client = httpx.AsyncClient(timeout=INVENTORY_TIMEOUT_S)


async def reserve_items(order_id, items):
    """Reserves stock for every line item on one order.

    Runs once per order, before the order row is written.
    """
    reserved = []
    for item in items:
        response = await _client.post(
            INVENTORY_BASE_URL + "/v1/reserve",
            json={
                "order_id": order_id,
                "product_id": item.product_id,
                "quantity": item.quantity,
            },
        )
        if response.status_code != 200:
            await _release_all(order_id)
            raise ReservationFailure(item.product_id, response.status_code)
        reserved.append(item.product_id)
    return reserved


async def _release_all(order_id):
    """Drops every hold for one order after a declined reservation."""
    await _client.post(
        INVENTORY_BASE_URL + "/v1/release", json={"order_id": order_id}
    )
