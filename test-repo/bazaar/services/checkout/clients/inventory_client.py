"""HTTP client for the inventory service.

Reservation during order placement lives in orders/reserve_step.py;
this module covers the remaining inventory calls.
"""

import httpx

from services.checkout.config import INVENTORY_BASE_URL, INVENTORY_TIMEOUT_S

# One client per process, reused across requests.
_client = httpx.AsyncClient(base_url=INVENTORY_BASE_URL, timeout=INVENTORY_TIMEOUT_S)


async def stock_level(product_id):
    """Reads the available stock for one product.

    Called from the cart-edit handler when a client sets a quantity, a
    few times per second.
    """
    response = await _client.get("/v1/stock/" + str(product_id))
    response.raise_for_status()
    return response.json()["available"]


async def release_order(order_id):
    """Releases every reservation held by one order.

    Called when an order is cancelled, a few times per minute.
    """
    response = await _client.post("/v1/release", json={"order_id": order_id})
    response.raise_for_status()
    return response.json()


async def reservation_status(order_id):
    """Reads the hold state for one order. Called from support tooling."""
    response = await _client.get("/v1/reservations/" + str(order_id))
    response.raise_for_status()
    return response.json()


async def close():
    """Closes the shared client. Called once at process shutdown."""
    await _client.aclose()
