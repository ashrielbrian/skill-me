"""Money math for carts and orders. Pure functions, no I/O.

All amounts are integer cents; each line's tax rounds on its own so the
stored lines sum exactly to the stored total.
"""

from libs.commerce.money import format_cents

# Flat shipping in cents by destination region.
_SHIPPING_BY_REGION = {
    "us": 599,
    "ca": 799,
    "eu": 899,
    "uk": 849,
    "au": 1099,
    "jp": 999,
}
_DEFAULT_SHIPPING_CENTS = 1299
_FREE_SHIPPING_THRESHOLD_CENTS = 7500


def subtotal_cents(items):
    """Sums unit price times quantity across the line items."""
    return sum(item.unit_price_cents * item.quantity for item in items)


def tax_cents(items):
    """Sums per-line tax, rounding each line to the nearest cent."""
    return sum(
        round(item.unit_price_cents * item.quantity * item.tax_rate)
        for item in items
    )


def shipping_cents(subtotal, region):
    """Returns the flat charge; orders at or over the threshold ship free."""
    if subtotal >= _FREE_SHIPPING_THRESHOLD_CENTS:
        return 0
    return _SHIPPING_BY_REGION.get(region, _DEFAULT_SHIPPING_CENTS)


def grand_total_cents(items, region):
    """Subtotal plus tax plus shipping, in cents."""
    subtotal = subtotal_cents(items)
    return subtotal + tax_cents(items) + shipping_cents(subtotal, region)


def display_total(items, region):
    """Formats the grand total for receipts and the cart view."""
    return format_cents(grand_total_cents(items, region), "USD")


def line_breakdown(item):
    """Returns the stored money fields for one line."""
    line_subtotal = item.unit_price_cents * item.quantity
    return {
        "product_id": item.product_id,
        "quantity": item.quantity,
        "unit_price_cents": item.unit_price_cents,
        "line_subtotal_cents": line_subtotal,
        "line_tax_cents": round(line_subtotal * item.tax_rate),
    }


def cart_breakdown(items, region):
    """Full money summary for the cart view and receipts."""
    subtotal = subtotal_cents(items)
    tax = tax_cents(items)
    shipping = shipping_cents(subtotal, region)
    total = subtotal + tax + shipping
    return {
        "lines": [line_breakdown(item) for item in items],
        "subtotal_cents": subtotal,
        "tax_cents": tax,
        "shipping_cents": shipping,
        "total_cents": total,
        "display_total": format_cents(total, "USD"),
    }
