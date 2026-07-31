"""Order data shapes shared across services.

Services exchange orders as rows and events, then rebuild these
shapes at the edge. Keeping the arithmetic here means subtotals
agree between checkout, pricing, and settlement.
"""

from dataclasses import dataclass, field

ORDER_STATUSES = (
    "pending",
    "paid",
    "packed",
    "shipped",
    "delivered",
    "canceled",
    "refunded",
)

_TERMINAL = frozenset({"delivered", "canceled", "refunded"})

# States from which a refund may be issued.
_REFUNDABLE = frozenset({"paid", "delivered"})

# Allowed forward moves; anything absent here is rejected.
_TRANSITIONS = {
    "pending": frozenset({"paid", "canceled"}),
    "paid": frozenset({"packed", "canceled", "refunded"}),
    "packed": frozenset({"shipped", "canceled"}),
    "shipped": frozenset({"delivered"}),
    "delivered": frozenset({"refunded"}),
    "canceled": frozenset(),
    "refunded": frozenset(),
}


@dataclass(frozen=True)
class OrderLine:
    """One purchasable line on an order."""

    sku: str
    quantity: int
    unit_price_cents: int
    category: str


@dataclass
class Order:
    """An order as services pass it around."""

    order_id: str
    region: str
    currency: str
    status: str = "pending"
    lines: list = field(default_factory=list)


def line_total_cents(line):
    """Total for one line in minor units."""
    return line.quantity * line.unit_price_cents


def subtotal_cents(order):
    """Pre-tax, pre-discount order total in minor units."""
    return sum(line_total_cents(line) for line in order.lines)


def is_terminal(status):
    """True when no further status moves are allowed."""
    return status in _TERMINAL


def can_move(status, next_status):
    """True when the status change is an allowed forward move."""
    return next_status in _TRANSITIONS.get(status, frozenset())


def with_status(order, next_status):
    """Copy the order with a validated status move applied."""
    if not can_move(order.status, next_status):
        raise ValueError("cannot move %s to %s" % (order.status, next_status))
    return Order(
        order.order_id,
        order.region,
        order.currency,
        next_status,
        list(order.lines),
    )


def refundable_cents(order):
    """Amount eligible for refund in the order's current state."""
    if order.status in _REFUNDABLE:
        return subtotal_cents(order)
    return 0
