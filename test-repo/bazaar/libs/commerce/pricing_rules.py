"""Deterministic price adjustments applied at quote time.

All functions are pure: integer cents in, integer cents out. The
rules run in one fixed order — markup, volume adjustment, clamp —
and every service uses this module so quotes agree everywhere.
"""

from .errors import ValidationError

# Category markups in basis points over supplier cost; categories
# absent here take the default.
MARKUP_BPS = {
    "electronics": 1200,
    "computers": 1100,
    "mobile_phones": 1150,
    "grocery": 2400,
    "beverages": 2600,
    "apparel": 4200,
    "footwear": 4000,
    "furniture": 3500,
    "jewelry": 5500,
    "watches": 5200,
}
DEFAULT_MARKUP_BPS = 3000

# Volume adjustment in basis points, largest threshold first.
VOLUME_TIERS = (
    (1_000_000, 250),
    (250_000, 125),
    (50_000, 50),
    (0, 0),
)

PRICE_FLOOR_CENTS = 1


def apply_markup(cost_cents, category):
    """Return the listed price for a supplier cost."""
    bps = MARKUP_BPS.get(category, DEFAULT_MARKUP_BPS)
    return cost_cents + (cost_cents * bps) // 10_000


def volume_adjustment_bps(subtotal_cents):
    """Basis points taken off for larger order subtotals."""
    for threshold, bps in VOLUME_TIERS:
        if subtotal_cents >= threshold:
            return bps
    return 0


def clamp_price(cents, floor=PRICE_FLOOR_CENTS, ceiling=None):
    """Keep a computed price inside the allowed band."""
    if cents < floor:
        return floor
    if ceiling is not None and cents > ceiling:
        return ceiling
    return cents


def quote_unit_price(cost_cents, category, subtotal_cents=0):
    """Run the full rule order for one line's unit price."""
    if cost_cents < 0:
        raise ValidationError("cost_cents", "negative")
    price = apply_markup(cost_cents, category)
    price -= (price * volume_adjustment_bps(subtotal_cents)) // 10_000
    return clamp_price(price)


def fee_cents(order_total_cents, fee_bps):
    """Marketplace fee on one order total, rounded down."""
    return (order_total_cents * fee_bps) // 10_000


def payout_cents(order_total_cents, fee_bps):
    """Seller payout after the marketplace fee."""
    return order_total_cents - fee_cents(order_total_cents, fee_bps)
