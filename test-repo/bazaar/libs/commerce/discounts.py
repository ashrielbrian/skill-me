"""Discount evaluation for carts and promotions.

Offers are plain tuples so they can travel through events without
a schema dependency: ``("percent", basis_points)`` or
``("amount", cents)``. Evaluation is pure integer arithmetic and
never takes a subtotal below zero.
"""

from .errors import ValidationError

OFFER_KINDS = frozenset({"percent", "amount"})

# Stacking never applies more than this many offers to one subtotal.
MAX_STACKED_OFFERS = 3


def check_offer(offer):
    """Validate one offer tuple and return it."""
    kind, value = offer
    if kind not in OFFER_KINDS:
        raise ValidationError("offer", "unknown kind %r" % kind)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError("offer", "value must be a non-negative integer")
    return offer


def discount_cents(subtotal_cents, offer):
    """Cents taken off the subtotal by a single offer."""
    kind, value = check_offer(offer)
    if kind == "percent":
        return min(subtotal_cents, (subtotal_cents * value) // 10_000)
    return min(subtotal_cents, value)


def best_offer(subtotal_cents, offers):
    """Return the single offer worth the most on this subtotal."""
    chosen = None
    chosen_value = -1
    for offer in offers:
        value = discount_cents(subtotal_cents, offer)
        if value > chosen_value:
            chosen, chosen_value = offer, value
    return chosen


def apply_stacked(subtotal_cents, offers):
    """Apply up to ``MAX_STACKED_OFFERS`` offers, largest first.

    Each offer applies to what remains after the one before it, so
    stacking two ten-percent offers takes less than twenty percent.
    """
    ranked = sorted(
        offers,
        key=lambda offer: discount_cents(subtotal_cents, offer),
        reverse=True,
    )
    remaining = subtotal_cents
    for offer in ranked[:MAX_STACKED_OFFERS]:
        remaining -= discount_cents(remaining, offer)
    return max(remaining, 0)


def total_discount_cents(subtotal_cents, offers):
    """Cents removed by ``apply_stacked`` on this subtotal."""
    return subtotal_cents - apply_stacked(subtotal_cents, offers)


def describe_offer(offer):
    """Human-readable form for receipts and admin views."""
    kind, value = check_offer(offer)
    if kind == "percent":
        return "%.2f%% off" % (value / 100)
    return "%d cents off" % value
