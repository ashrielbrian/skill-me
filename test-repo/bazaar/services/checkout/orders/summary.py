"""Order status view backing the polling endpoint."""

import json

from libs.dbkit.executor import fetch_one
from services.checkout.errors import OrderNotFound

_STATUS_QUERY = "SELECT status, items_json, updated_at FROM orders WHERE id = $1"


async def order_status_view(order_id):
    """Returns the status summary for one order.

    Clients poll this endpoint about 40 times per second at peak.
    """
    row = await fetch_one(_STATUS_QUERY, (order_id,))
    if row is None:
        raise OrderNotFound(order_id)
    items = json.loads(row["items_json"])
    total_cents = sum(
        entry["unit_price_cents"] * entry["quantity"] for entry in items
    )
    return {
        "order_id": order_id,
        "status": row["status"],
        "item_count": len(items),
        "total_cents": total_cents,
        "updated_at": str(row["updated_at"]),
    }
