"""Proxy for the checkout service order endpoints.

Order placement writes an audit record after checkout accepts the call;
status reads pass straight through.
"""
import httpx

from libs.commerce.audit import audit_context, write_audit

from services.gateway import responses
from services.gateway.config import CHECKOUT_BASE_URL, REQUEST_TIMEOUT_S

_client = httpx.AsyncClient(base_url=CHECKOUT_BASE_URL, timeout=REQUEST_TIMEOUT_S)


async def place_order(request):
    """Forwards an order submission to checkout. Runs for each order placement."""
    session = request.get("session")
    if session is None:
        return responses.failure("unauthorized", "orders require a session", 401)
    body = await request.json()
    resp = await _client.post(
        "/v1/orders", json=body, headers={"x-tenant-id": session.tenant_id}
    )
    if resp.status_code >= 500:
        return responses.failure("upstream_error", "checkout did not answer", 502)
    async with audit_context(session.tenant_id) as ctx:
        await write_audit(ctx, actor_id=session.user_id, action="order.place")
    return responses.envelope(resp.json(), status=resp.status_code)


async def list_orders(request):
    """Forwards an order history read. Runs when a client opens its order list."""
    session = request.get("session")
    if session is None:
        return responses.failure("unauthorized", "orders require a session", 401)
    resp = await _client.get("/v1/orders", params={"user_id": session.user_id})
    if resp.status_code >= 500:
        return responses.failure("upstream_error", "checkout did not answer", 502)
    return responses.envelope(resp.json())


async def get_order(request):
    """Forwards an order status read. Runs about 40 times per second at peak."""
    order_id = request.match_info["order_id"]
    resp = await _client.get("/v1/orders/" + order_id)
    if resp.status_code == 404:
        return responses.failure("not_found", "unknown order", 404)
    if resp.status_code >= 500:
        return responses.failure("upstream_error", "checkout did not answer", 502)
    return responses.envelope(resp.json())


async def close():
    """Releases the checkout client. Runs once at process exit."""
    await _client.aclose()
