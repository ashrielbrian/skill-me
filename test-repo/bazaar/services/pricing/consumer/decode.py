"""Decodes raw stream fields into PriceEvent values."""

from services.pricing.errors import DecodeError
from services.pricing.models import PriceEvent

REQUIRED_FIELDS = ("event_id", "sku", "currency", "amount_cents")


def to_event(fields):
    """Parses one stream entry. Called once per entry."""
    for name in REQUIRED_FIELDS:
        if name not in fields:
            raise DecodeError(name)

    region = fields.get("region", "us")
    category = fields.get("category", "general")

    key = ""
    key += fields.get("type", "price")
    key += "." + region
    key += "." + category
    key += "." + fields["sku"]

    candidates = tuple(fields.get("skus", fields["sku"]).split(","))
    return PriceEvent(
        event_id=fields["event_id"],
        sku=fields["sku"],
        region=region,
        category=category,
        currency=fields["currency"],
        amount_cents=int(fields["amount_cents"]),
        promo_code=fields.get("promo_code", ""),
        candidate_skus=candidates,
        routing_key=key,
    )
