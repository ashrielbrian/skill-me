import logging
import time

from ..clients.carrier import get_client
from ..config import settings
from ..repos import refunds as refunds_repo
from ..repos import shipments as shipments_repo
from ..utils import dollars_to_cents
from .refund_strategy import get_strategy

log = logging.getLogger(__name__)

# ============================================================
# Refund service
# ============================================================


def refund_shipment(shipment_id: int, amount, reason: str, idempotency_key: str) -> dict:
    """Refund a shipment."""
    # Check if this request was already processed
    existing = refunds_repo.get_by_idempotency_key(idempotency_key)
    if existing:
        return existing

    # Get the shipment
    shipment = shipments_repo.get(shipment_id)

    # Convert the amount to cents
    requested_cents = dollars_to_cents(amount) if amount is not None else None

    # Compute the refund amount
    strategy = get_strategy(settings.refund_strategy)
    amount_cents = strategy.compute(shipment, requested_cents)

    # Insert the refund
    refund_id = refunds_repo.insert(shipment_id, amount_cents, reason, idempotency_key)

    # Update the shipment status
    shipments_repo.set_status(shipment_id, "refunded")
    shipments_repo.add_event(shipment_id, "refunded")

    # Void the label with the carrier
    _void_label(shipment)

    log.info("refund %s created for shipment %s", refund_id, shipment_id)
    return refunds_repo.get(refund_id)


def _void_label(shipment: dict) -> None:
    """Void the shipment's label with the carrier."""
    client = get_client()
    for attempt in range(3):
        try:
            client.void_label(shipment["carrier"], shipment["label_id"])
            return
        except Exception:
            time.sleep(2)
