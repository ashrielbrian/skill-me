"""Row and payload shapes shared across the orders package.

items_json stores the full product snapshot for each line item, about
25 KB per order; it is written at order placement and read by the
settlement export.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    """One order line. Price fields are filled in by the pricing step."""

    product_id: str
    category: str
    quantity: int
    unit_price_cents: int = 0
    tax_rate: float = 0.0


@dataclass(frozen=True)
class OrderContext:
    """Rows loaded before pricing: buyer, cart, and shipping address."""

    buyer: dict
    cart: dict
    address: dict


@dataclass
class Order:
    """One order header, mirrors the orders table."""

    id: str
    buyer_id: str
    status: str
    currency: str
    total_cents: int
    items_json: str


# Allowed forward moves for one order's status.
_NEXT_STATUSES = {
    "pending": frozenset({"reserved", "cancelled"}),
    "reserved": frozenset({"placed", "cancelled"}),
    "placed": frozenset({"settled", "cancelled"}),
    "settled": frozenset(),
    "cancelled": frozenset(),
}
_NO_NEXT = frozenset()


def can_transition(current, target):
    """True when an order may move from current to target status."""
    return target in _NEXT_STATUSES.get(current, _NO_NEXT)


def order_public_view(order):
    """The fields returned to the client after placement."""
    return {
        "order_id": order.id,
        "status": order.status,
        "total_cents": order.total_cents,
        "currency": order.currency,
    }


def line_item_from_payload(entry):
    """Builds a LineItem from one request payload entry."""
    return LineItem(
        product_id=str(entry["product_id"]),
        category=str(entry.get("category", "general")),
        quantity=int(entry["quantity"]),
    )


def item_record(item, address):
    """Builds the stored record for one line on items_json."""
    return {
        "product_id": item.product_id,
        "category": item.category,
        "quantity": item.quantity,
        "unit_price_cents": item.unit_price_cents,
        "tax_rate": item.tax_rate,
        "ship_to_region": address.get("region"),
        "ship_to_country": address.get("country"),
    }
