"""Domain records for reservations."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class ReservationStatus(enum.StrEnum):
    """Lifecycle of a reservation row."""

    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"


@dataclass(frozen=True, slots=True)
class Reservation:
    """One reserved SKU/quantity pair held for an order."""

    id: int
    order_id: str
    sku: str
    quantity: int
    bin_label: str
    status: ReservationStatus
    expires_at: datetime

    def to_wire(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "sku": self.sku,
            "quantity": self.quantity,
            "bin": self.bin_label,
            "status": self.status.value,
            "expires_at": self.expires_at.isoformat(),
        }
