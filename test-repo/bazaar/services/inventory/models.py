"""Wire models for the inventory service.

routes.py parses each request body into these types once; domain code under
reservations/ and stock/ receives them already validated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.inventory.config import RESERVE_BATCH_MAX
from services.inventory.errors import BatchTooLargeError, ValidationError

# Compiled once at import; matched once per request item.
_SKU_RE = re.compile(r"^[A-Z0-9]{2,12}-[A-Z0-9]{4,16}$")

_KNOWN_ZONES = frozenset({"us-east", "us-west", "eu-central", "ap-south"})

_MAX_QUANTITY = 500


def validate_sku(sku: str) -> str:
    if _SKU_RE.match(sku) is None:
        raise ValidationError(f"malformed sku: {sku}")
    return sku


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"missing or invalid field: {key}")
    return value


def _require_zone(payload: dict) -> str:
    zone = _require_str(payload, "zone")
    if zone not in _KNOWN_ZONES:
        raise ValidationError(f"unknown zone: {zone}")
    return zone


@dataclass(frozen=True, slots=True)
class ReserveItem:
    """One SKU/quantity pair within a reservation request."""

    sku: str
    quantity: int

    @classmethod
    def from_payload(cls, payload: object) -> "ReserveItem":
        if not isinstance(payload, dict):
            raise ValidationError("each item must be an object")
        sku = validate_sku(_require_str(payload, "sku"))
        quantity = payload.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise ValidationError("quantity must be an integer")
        if quantity < 1 or quantity > _MAX_QUANTITY:
            raise ValidationError(f"quantity out of range: {quantity}")
        return cls(sku=sku, quantity=quantity)


@dataclass(frozen=True, slots=True)
class ReserveRequest:
    """Parsed body of POST /v1/reserve."""

    order_id: str
    zone: str
    item: ReserveItem

    @classmethod
    def from_payload(cls, payload: dict) -> "ReserveRequest":
        order_id = _require_str(payload, "order_id")
        zone = _require_zone(payload)
        item = ReserveItem.from_payload(payload.get("item"))
        return cls(order_id=order_id, zone=zone, item=item)


@dataclass(frozen=True, slots=True)
class StockLevel:
    """Current counts for one stock row."""

    sku: str
    available: int
    reserved: int

    def to_wire(self) -> dict:
        return {"sku": self.sku, "available": self.available, "reserved": self.reserved}


def parse_batch_payload(payload: dict) -> tuple[str, str, list[ReserveItem]]:
    """Validate the body of POST /v1/reserve/batch."""
    order_id = _require_str(payload, "order_id")
    zone = _require_zone(payload)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("items must be a non-empty list")
    if len(raw_items) > RESERVE_BATCH_MAX:
        raise BatchTooLargeError(len(raw_items), RESERVE_BATCH_MAX)
    items = [ReserveItem.from_payload(raw) for raw in raw_items]
    skus = [item.sku for item in items]
    if len(set(skus)) != len(skus):
        raise ValidationError("duplicate sku in batch")
    return order_id, zone, items
