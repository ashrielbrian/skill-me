"""Request validation for order placement. Pure functions, no I/O."""

import re

from services.checkout.config import (
    MAX_LINE_ITEMS,
    MAX_QUANTITY_PER_ITEM,
    SUPPORTED_REGIONS,
)
from services.checkout.errors import ValidationFailure

# Entity ids look like "buyer_a1b2c3d4": a lowercase prefix, then hex-ish tail.
_ID_PATTERN = re.compile(r"^[a-z]+_[A-Za-z0-9]{6,32}$")
_CATEGORY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def validate_order_request(payload):
    """Checks one order placement payload. Runs once per order request.

    Collects every reason before raising so the client sees the full
    list in a single response.
    """
    reasons = []
    buyer_id = payload.get("buyer_id")
    cart_id = payload.get("cart_id")
    region = payload.get("region", "")
    items = payload.get("items") or []
    promo_code = payload.get("promo_code")

    if not isinstance(buyer_id, str) or not _ID_PATTERN.match(buyer_id):
        reasons.append("buyer_id is missing or malformed")
    if not isinstance(cart_id, str) or not _ID_PATTERN.match(cart_id):
        reasons.append("cart_id is missing or malformed")
    if region not in SUPPORTED_REGIONS:
        reasons.append("region %r is not supported" % region)
    if not items:
        reasons.append("order has no line items")
    if len(items) > MAX_LINE_ITEMS:
        reasons.append("order has more than %d line items" % MAX_LINE_ITEMS)
    if promo_code is not None and not isinstance(promo_code, str):
        reasons.append("promo_code must be a string when present")

    for index, entry in enumerate(items):
        if not isinstance(entry, dict):
            reasons.append("item %d is not an object" % index)
            continue
        if "product_id" not in entry:
            reasons.append("item %d is missing product_id" % index)
        quantity = entry.get("quantity")
        if not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY_PER_ITEM:
            reasons.append("item %d has an invalid quantity" % index)

    if reasons:
        raise ValidationFailure(reasons)
    return {
        "buyer_id": buyer_id,
        "cart_id": cart_id,
        "region": region,
        "items": items,
        "promo_code": promo_code,
    }


def validate_cart_item_payload(payload):
    """Checks one cart line payload. Runs once per cart edit."""
    reasons = []
    product_id = payload.get("product_id")
    category = payload.get("category", "general")
    quantity = payload.get("quantity")
    region = payload.get("region", "us")

    if not isinstance(product_id, str) or not _ID_PATTERN.match(product_id):
        reasons.append("product_id is missing or malformed")
    if not isinstance(category, str) or not _CATEGORY_PATTERN.match(category):
        reasons.append("category is malformed")
    if not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY_PER_ITEM:
        reasons.append(
            "quantity must be an integer between 1 and %d" % MAX_QUANTITY_PER_ITEM
        )
    if region not in SUPPORTED_REGIONS:
        reasons.append("region %r is not supported" % region)

    if reasons:
        raise ValidationFailure(reasons)
    return {
        "product_id": product_id,
        "category": category,
        "quantity": quantity,
        "region": region,
    }
