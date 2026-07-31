"""Category price bounds.

Shared by the admin CLI and the backfill in tools/; both validate
prices against these bounds before writing.
"""

from services.pricing.errors import GuardrailViolation

# category -> (minimum cents, maximum cents)
CATEGORY_BOUNDS = {
    "general": (10, 5_000_000),
    "digital": (0, 1_000_000),
    "grocery": (10, 200_000),
    "furniture": (500, 20_000_000),
    "jewelry": (100, 50_000_000),
}

DEFAULT_BOUNDS = CATEGORY_BOUNDS["general"]


def bounds_for(category):
    """Returns (minimum, maximum) cents for a category."""
    return CATEGORY_BOUNDS.get(category, DEFAULT_BOUNDS)


def within_bounds(amount_cents, category):
    """True when the amount is inside the category bounds."""
    low, high = bounds_for(category)
    return low <= amount_cents <= high


def check(sku, amount_cents, category):
    """Raises GuardrailViolation when a price is out of bounds."""
    if not within_bounds(amount_cents, category):
        raise GuardrailViolation(sku, amount_cents)


def clamp(amount_cents, category):
    """Returns the amount pinned into the category bounds."""
    low, high = bounds_for(category)
    return min(max(amount_cents, low), high)
