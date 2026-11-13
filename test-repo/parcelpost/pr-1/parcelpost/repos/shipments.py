from .. import db
from ..errors import NotFound


def get(shipment_id: int) -> dict:
    row = db.query_one("SELECT * FROM shipments WHERE id = ?", (shipment_id,))
    if row is None:
        raise NotFound(f"shipment {shipment_id} does not exist")
    return row


def list_for_customer(customer_id: int, limit: int, after_id: int = 0) -> list[dict]:
    return db.query(
        "SELECT * FROM shipments WHERE customer_id = ? AND id >= ? ORDER BY id LIMIT ?",
        (customer_id, after_id, limit),
    )


def insert(
    customer_id: int,
    carrier: str,
    label_id: str,
    weight_g: int,
    dest_country: str,
    charge_cents: int,
    declared_cents: int | None,
) -> int:
    return db.execute(
        "INSERT INTO shipments (customer_id, carrier, label_id, weight_g, dest_country,"
        " charge_cents, declared_cents) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (customer_id, carrier, label_id, weight_g, dest_country, charge_cents, declared_cents),
    )


def add_event(shipment_id: int, kind: str) -> int:
    return db.execute(
        "INSERT INTO shipment_events (shipment_id, kind) VALUES (?, ?)", (shipment_id, kind)
    )


def set_status(shipment_id: int, status: str) -> None:
    db.execute("UPDATE shipments SET status = ? WHERE id = ?", (status, shipment_id))
