"""Event envelope schema shared by producers and consumers.

Every event on the wire is an envelope: identity, type, timestamp,
attempt counter, and the domain payload. Producers build envelopes
with ``make_envelope``; consumers gate on ``validate_event`` before
touching the payload.
"""

import uuid
from datetime import datetime, timezone

from .errors import EventkitError

ENVELOPE_VERSION = 2

REQUIRED_FIELDS = ("event_id", "event_type", "occurred_at", "payload")

_FIELD_TYPES = {
    "event_id": str,
    "event_type": str,
    "occurred_at": str,
    "payload": dict,
    "attempt": int,
    "version": int,
}


# Event types currently on the wire, named ``domain.past_tense``.
KNOWN_EVENT_TYPES = frozenset({
    "catalog.product_listed",
    "catalog.product_updated",
    "catalog.product_delisted",
    "pricing.price_set",
    "pricing.promo_started",
    "pricing.promo_ended",
    "checkout.order_placed",
    "checkout.order_paid",
    "checkout.order_canceled",
    "inventory.stock_reserved",
    "inventory.stock_released",
    "inventory.stock_adjusted",
    "settlement.payout_created",
    "settlement.payout_settled",
})


class SchemaError(EventkitError):
    """An envelope is missing fields or carries wrong types."""


def is_known_type(event_type):
    """True when the type is in the registered set."""
    return event_type in KNOWN_EVENT_TYPES


def make_envelope(event_type, payload):
    """Wrap a payload in a fresh envelope with identity and time."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": dict(payload),
        "version": ENVELOPE_VERSION,
        "attempt": 0,
    }


def validate_event(event):
    """Raise ``SchemaError`` unless the envelope is well formed."""
    for field in REQUIRED_FIELDS:
        if field not in event:
            raise SchemaError("missing field: %s" % field)
    for field, expected in _FIELD_TYPES.items():
        if field in event and not isinstance(event[field], expected):
            raise SchemaError(
                "field %s must be %s" % (field, expected.__name__)
            )
    return event
