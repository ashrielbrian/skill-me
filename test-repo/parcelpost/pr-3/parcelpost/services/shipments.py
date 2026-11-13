import logging

from .. import db
from ..clients.carrier import get_client
from ..repos import shipments as shipments_repo
from ..retry import with_retry

log = logging.getLogger(__name__)


def create_shipment(
    customer_id: int,
    carrier: str,
    weight_g: int,
    dest_country: str,
    declared_cents: int | None = None,
) -> dict:
    client = get_client()
    charge_cents = with_retry(lambda: client.get_rate(carrier, weight_g, dest_country))
    # Not retried: create_label is not idempotent at the carrier, and a retry
    # after a timeout can buy a second label.
    label_id = client.create_label(carrier, weight_g, dest_country)

    # The row and its first event commit together, so a shipment never exists
    # without its 'labelled' event.
    with db.transaction():
        shipment_id = shipments_repo.insert(
            customer_id, carrier, label_id, weight_g, dest_country, charge_cents, declared_cents
        )
        shipments_repo.add_event(shipment_id, "labelled")

    log.info("shipment %s created for customer %s, label %s", shipment_id, customer_id, label_id)
    return shipments_repo.get(shipment_id)


def create_shipments_bulk(customer_id: int, items: list[dict]) -> list[dict]:
    client = get_client()
    shipment_ids = []
    # One transaction for the batch: a failure on any item rolls back every row.
    with db.transaction():
        for item in items:
            charge_cents = with_retry(
                lambda: client.get_rate(item["carrier"], item["weight_g"], item["dest_country"])
            )
            label_id = client.create_label(item["carrier"], item["weight_g"], item["dest_country"])
            shipment_ids.append(
                shipments_repo.insert(
                    customer_id,
                    item["carrier"],
                    label_id,
                    item["weight_g"],
                    item["dest_country"],
                    charge_cents,
                    item["declared_cents"],
                )
            )

    log.info("%s shipments created for customer %s", len(shipment_ids), customer_id)
    placeholders = ", ".join("?" * len(shipment_ids))
    return db.query(
        f"SELECT * FROM shipments WHERE id IN ({placeholders}) ORDER BY id", tuple(shipment_ids)
    )


def get_shipment(shipment_id: int) -> dict:
    return shipments_repo.get(shipment_id)


def list_shipments(customer_id: int, limit: int, after_id: int = 0) -> dict:
    items = shipments_repo.list_for_customer(customer_id, limit, after_id)
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}
