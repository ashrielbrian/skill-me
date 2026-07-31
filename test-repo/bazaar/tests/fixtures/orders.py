"""Order builders used by the checkout and settlement suites."""


def build_line_item(sku="WID-0001-BLK", qty=1, unit_cents=1999):
    return {
        "sku": sku,
        "qty": qty,
        "unit_cents": unit_cents,
        "line_cents": qty * unit_cents,
    }


def build_order(order_id="ord_0001", item_count=8, unit_cents=1250, currency="USD"):
    """Default order mirrors the production average of eight line items."""
    items = []
    for index in range(item_count):
        sku = "WID-%04d-BLK" % (index + 1)
        items.append(build_line_item(sku=sku, qty=1, unit_cents=unit_cents))
    subtotal = sum(item["line_cents"] for item in items)
    return {
        "order_id": order_id,
        "currency": currency,
        "items": items,
        "subtotal_cents": subtotal,
        "status": "pending",
    }


def order_rows(count=3, start=1):
    """Rows shaped like the checkout orders table, for FakeConn replays."""
    rows = []
    for offset in range(count):
        rows.append({
            "order_id": "ord_%04d" % (start + offset),
            "subtotal_cents": 10_000 + 250 * offset,
            "status": "placed",
        })
    return rows


def render_order_summary(order):
    # One line per item, assembled with a single join at the end.
    parts = ["order %s (%s)" % (order["order_id"], order["currency"])]
    for item in order["items"]:
        parts.append("  %s x%d @ %d" % (item["sku"], item["qty"], item["unit_cents"]))
    parts.append("subtotal %d" % order["subtotal_cents"])
    return "\n".join(parts)
