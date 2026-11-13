"""Full-order refunds."""

import json
import uuid

from libs.dbkit import transaction
from libs.eventkit import make_envelope, publish, stream_name
from services.checkout.errors import OrderNotFound, ValidationFailure
from services.checkout.orders.models import can_transition
from services.checkout.webhooks.notify import dispatch

_REFUND_ID_PREFIX = "rfd_"

_LOCK_ORDER = """
    SELECT id, buyer_id, status, items_json FROM orders WHERE id = $1 FOR UPDATE
"""
_INSERT_REFUND = """
    INSERT INTO refunds (id, order_id, amount_cents, reason, created_at)
    VALUES ($1, $2, $3, $4, now())
"""
_MARK_REFUNDED = "UPDATE orders SET status = 'refunded', updated_at = now() WHERE id = $1"
_SELLER_QUERY = "SELECT seller_id FROM settled_orders WHERE order_id = $1"
_INSERT_ADJUSTMENT = """
    INSERT INTO payout_adjustments (seller_id, amount_cents, reason, day)
    VALUES ($1, $2, 'refund', CURRENT_DATE)
"""


def refund_amount_cents(items):
    """Amount to return to the buyer: every line with its tax."""
    total = 0
    for entry in items:
        line_cents = entry["unit_price_cents"] * entry["quantity"]
        total += int(line_cents * (1 + entry["tax_rate"]))
    return total


async def refund_order(order_id, reason):
    """Refunds one order in full. Called a few times per minute.

    The order row stays locked from the status check to the status
    update, so two requests for the same order cannot both refund it.
    """
    async with transaction() as conn:
        row = await conn.fetchrow(_LOCK_ORDER, order_id)
        if row is None:
            raise OrderNotFound(order_id)
        if not can_transition(row["status"], "refunded"):
            raise ValidationFailure(
                ["order in status %s cannot be refunded" % row["status"]]
            )
        amount_cents = refund_amount_cents(json.loads(row["items_json"]))
        refund_id = _REFUND_ID_PREFIX + uuid.uuid4().hex
        await conn.execute(_INSERT_REFUND, refund_id, order_id, amount_cents, reason)
        await conn.execute(_MARK_REFUNDED, order_id)
        if row["status"] == "settled":
            # The seller was already paid for this order; settlement folds
            # the adjustment into the seller's next payout.
            seller = await conn.fetchrow(_SELLER_QUERY, order_id)
            await conn.execute(_INSERT_ADJUSTMENT, seller["seller_id"], amount_cents)

    event = {
        "order_id": order_id,
        "refund_id": refund_id,
        "amount_cents": amount_cents,
        "status": "refunded",
    }
    publish(stream_name("checkout", "order"), make_envelope("checkout.order_refunded", event))
    await dispatch("order.refunded", event)
    return event
