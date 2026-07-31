"""HTTP route handlers for the checkout service.

Thin layer: parse the request, call the domain function, map errors to
status codes. All business logic lives in orders/, carts/, webhooks/.
"""

import logging

from aiohttp import web

from services.checkout.carts import repo, rules
from services.checkout.clients.inventory_client import stock_level
from services.checkout.errors import CheckoutError, status_code_for
from services.checkout.orders import create, summary
from services.checkout.orders.validation import validate_cart_item_payload
from services.checkout.webhooks import registry

logger = logging.getLogger(__name__)


def register(app):
    """Attaches every route. Called once from app.build_app()."""
    app.router.add_post("/v1/orders", create_order)
    app.router.add_get("/v1/orders/{order_id}", get_order_status)
    app.router.add_post("/v1/orders/{order_id}/cancel", cancel_order)
    app.router.add_get("/v1/carts/{cart_id}", get_cart)
    app.router.add_put("/v1/carts/{cart_id}/items", put_cart_item)
    app.router.add_delete("/v1/carts/{cart_id}/items/{product_id}", delete_cart_item)
    app.router.add_post("/v1/webhooks", post_webhook)


def _error_response(exc):
    return web.json_response({"error": str(exc)}, status=status_code_for(exc))


async def create_order(request):
    """Handles order placement, about 13 requests per second at peak."""
    payload = await request.json()
    try:
        result = await create.place_order(payload)
    except CheckoutError as exc:
        logger.warning("order placement rejected: %s", exc)
        return _error_response(exc)
    return web.json_response(result, status=201)


async def get_order_status(request):
    """Handles order status polling, about 40 requests per second at peak."""
    order_id = request.match_info["order_id"]
    try:
        view = await summary.order_status_view(order_id)
    except CheckoutError as exc:
        return _error_response(exc)
    return web.json_response(view)


async def cancel_order(request):
    """Handles order cancellation, a few requests per minute."""
    order_id = request.match_info["order_id"]
    try:
        result = await create.cancel_order(order_id)
    except CheckoutError as exc:
        return _error_response(exc)
    return web.json_response(result)


async def get_cart(request):
    cart_id = request.match_info["cart_id"]
    try:
        cart = await repo.load_cart(cart_id)
    except CheckoutError as exc:
        return _error_response(exc)
    return web.json_response(cart)


async def put_cart_item(request):
    """Adds or updates one cart line after a stock check."""
    cart_id = request.match_info["cart_id"]
    payload = await request.json()
    try:
        line = validate_cart_item_payload(payload)
        rules.check_item_allowed(line["region"], line["category"])
        available = await stock_level(line["product_id"])
        if available < line["quantity"]:
            return web.json_response(
                {"error": "quantity above available stock"}, status=409
            )
        await repo.set_item(
            cart_id, line["product_id"], line["category"], line["quantity"]
        )
    except CheckoutError as exc:
        return _error_response(exc)
    return web.json_response(
        {
            "cart_id": cart_id,
            "product_id": line["product_id"],
            "quantity": line["quantity"],
        }
    )


async def delete_cart_item(request):
    cart_id = request.match_info["cart_id"]
    product_id = request.match_info["product_id"]
    try:
        await repo.remove_item(cart_id, product_id)
    except CheckoutError as exc:
        return _error_response(exc)
    return web.json_response({"cart_id": cart_id, "removed": product_id})


async def post_webhook(request):
    """Registers a webhook callback. Admin surface, called rarely."""
    payload = await request.json()
    try:
        subscription_id = await registry.add_subscription(
            str(payload["topic"]), str(payload["url"])
        )
    except CheckoutError as exc:
        return _error_response(exc)
    return web.json_response({"subscription_id": subscription_id}, status=201)
