from .. import db
from ..errors import ValidationError

SORT_COLUMNS = {"created": "created_at", "amount": "amount_cents"}


def insert(shipment_id: int, amount_cents: int, reason: str, idempotency_key: str) -> int:
    return db.execute(
        "INSERT INTO refunds (shipment_id, amount_cents, reason, idempotency_key)"
        " VALUES (?, ?, ?, ?)",
        (shipment_id, amount_cents, reason, idempotency_key),
    )


def get(refund_id: int) -> dict | None:
    return db.query_one("SELECT * FROM refunds WHERE id = ?", (refund_id,))


def get_by_idempotency_key(idempotency_key: str) -> dict | None:
    return db.query_one(
        "SELECT * FROM refunds WHERE idempotency_key = ?", (idempotency_key,)
    )


def list_for_shipment(shipment_id: int, sort: str = "created") -> list[dict]:
    column = SORT_COLUMNS.get(sort)
    if column is None:
        raise ValidationError(f"sort must be one of: {', '.join(SORT_COLUMNS)}")
    return db.query(
        f"SELECT * FROM refunds WHERE shipment_id = ? ORDER BY {column}", (shipment_id,)
    )
