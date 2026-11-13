from abc import ABC, abstractmethod

from ..errors import ValidationError


class RefundStrategy(ABC):
    """Base class for refund strategies."""

    @abstractmethod
    def compute(self, shipment: dict, requested_cents: int | None) -> int:
        """Return the amount to refund, in cents."""


class StandardRefundStrategy(RefundStrategy):
    """Refunds the requested amount, or the full charge if none is given."""

    def compute(self, shipment: dict, requested_cents: int | None) -> int:
        if requested_cents is None:
            return shipment["charge_cents"]
        if requested_cents <= 0:
            raise ValidationError("amount must be positive")
        if requested_cents > shipment["charge_cents"]:
            raise ValidationError("amount exceeds the shipment charge")
        return requested_cents


_REGISTRY: dict[str, type[RefundStrategy]] = {
    "standard": StandardRefundStrategy,
}


def get_strategy(name: str) -> RefundStrategy:
    """Look up a refund strategy by name."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(f"unknown refund strategy: {name}")
