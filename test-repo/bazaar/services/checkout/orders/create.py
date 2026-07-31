"""Order placement and cancellation orchestration."""

import json
import uuid

from libs.commerce.audit import audit_context, write_audit
from libs.dbkit.executor import fetch_one
from services.checkout.carts.repo import clear_cart
from services.checkout.carts.rules import check_cart
from services.checkout.carts.totals import grand_total_cents
from services.checkout.clients.inventory_client import release_order
from services.checkout.errors import OrderNotFound, ValidationFailure
from services.checkout.orders.context import load_order_context
from services.checkout.orders.models import (
    Order,
    can_transition,
    item_record,
    line_item_from_payload,
    order_public_view,
)
from services.checkout.orders.pricing_step import price_items
from services.checkout.orders.reserve_step import reserve_items
from services.checkout.orders.txn import update_status, write_order
from services.checkout.orders.validation import validate_order_request
from services.checkout.promotions import apply_promo
from services.checkout.webhooks.notify import dispatch

_ORDER_ID_PREFIX = "ord_"
_STATUS_QUERY = "SELECT status FROM orders WHERE id = $1"


async def place_order(payload):
    """Places one order end to end.

    Runs about 13 times per second at peak (about 800 orders per
    minute). Steps: validate, load context, price, reserve stock,
    write, audit, notify.
    """
    request = validate_order_request(payload)
    ctx = await load_order_context(request["buyer_id"], request["cart_id"])
    items = [line_item_from_payload(entry) for entry in request["items"]]
    check_cart(items, request["region"])
    priced = await price_items(items, request["region"])
    total_cents = await _order_total(request, priced)
    order = _build_order(request, ctx, priced, total_cents)

    await reserve_items(order.id, priced)
    await write_order(order, priced)
    # Exactly one audit record per placed order.
    await write_audit(audit_context())
    await clear_cart(request["cart_id"])
    await dispatch("order.placed", _event_payload(order, priced))
    return order_public_view(order)


async def cancel_order(order_id):
    """Cancels one order. Called a few times per minute.

    Releases the inventory holds, moves the order to cancelled, and
    notifies subscribers.
    """
    row = await fetch_one(_STATUS_QUERY, (order_id,))
    if row is None:
        raise OrderNotFound(order_id)
    if not can_transition(row["status"], "cancelled"):
        raise ValidationFailure(
            ["order in status %s cannot be cancelled" % row["status"]]
        )
    await release_order(order_id)
    await update_status(order_id, "cancelled")
    await dispatch("order.cancelled", {"order_id": order_id, "status": "cancelled"})
    return {"order_id": order_id, "status": "cancelled"}


async def _order_total(request, priced):
    """Grand total in cents, after any promo code."""
    total_cents = grand_total_cents(priced, request["region"])
    if request["promo_code"] is not None:
        total_cents = await apply_promo(request["promo_code"], total_cents)
    return total_cents


def _build_order(request, ctx, priced, total_cents):
    """Assembles the order header, including the stored items_json."""
    records = [item_record(item, ctx.address) for item in priced]
    return Order(
        id=_ORDER_ID_PREFIX + uuid.uuid4().hex,
        buyer_id=request["buyer_id"],
        status="placed",
        currency="USD",
        total_cents=total_cents,
        items_json=json.dumps(records),
    )


def _event_payload(order, priced):
    """Builds the webhook event body for one placed order."""
    return {
        "order_id": order.id,
        "buyer_id": order.buyer_id,
        "status": order.status,
        "total_cents": order.total_cents,
        "item_count": len(priced),
    }
