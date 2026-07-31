"""Promo code application for carts."""

import re

from libs.dbkit.executor import fetch_one
from services.checkout.errors import ValidationFailure

_CODE_PATTERN = re.compile(r"^[A-Z0-9]{4,16}$")
_PROMO_QUERY = """
    SELECT code, kind, amount_cents, percent, min_subtotal_cents
    FROM promotions
    WHERE code = $1 AND active AND now() BETWEEN starts_at AND ends_at
"""


async def apply_promo(code, subtotal_cents):
    """Applies one promo code to a cart subtotal.

    Called once per checkout that carries a code, well under one call
    per second. Returns the discounted subtotal in cents.
    """
    if not _CODE_PATTERN.match(code):
        raise ValidationFailure(["promo code is malformed"])
    row = await fetch_one(_PROMO_QUERY, (code,))
    if row is None:
        raise ValidationFailure(["promo code %s is not active" % code])
    if subtotal_cents < row["min_subtotal_cents"]:
        raise ValidationFailure(
            ["subtotal is below the minimum for code %s" % code]
        )
    discount = _discount_cents(row, subtotal_cents)
    # A discount never takes the subtotal below zero.
    return max(subtotal_cents - discount, 0)


def _discount_cents(row, subtotal_cents):
    """Computes the discount for one promo row."""
    if row["kind"] == "amount":
        return row["amount_cents"]
    return round(subtotal_cents * row["percent"] / 100)
