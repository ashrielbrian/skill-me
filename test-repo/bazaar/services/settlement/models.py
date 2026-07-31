"""Row shapes shared across the settlement batch."""

from dataclasses import dataclass
from datetime import date


@dataclass(slots=True)
class PayoutLine:
    """One seller payout for one settlement day.

    Built once per seller per nightly run.
    """

    seller_id: str
    gross_cents: int
    fee_cents: int
    net_cents: int
    currency: str
    day: date


@dataclass(slots=True)
class Adjustment:
    """Manual correction applied to a seller's gross before fees."""

    seller_id: str
    amount_cents: int
    reason: str


@dataclass(slots=True)
class TransferInstruction:
    """One outbound bank transfer in the nightly batch."""

    seller_id: str
    amount_cents: int
    currency: str
    reference: str
