import uuid

from .. import db
from ..http import Request, Response, json_response, route
from ..services import refunds as refunds_service
from ..validation import parse_positive_int


def _serialize(refund: dict) -> dict:
    return {
        "refundId": refund["id"],
        "shipmentId": refund["shipment_id"],
        "amountCents": refund["amount_cents"],
        "reason": refund["reason"],
        "createdAt": refund["created_at"],
    }


@route("POST", "/shipments/{id}/refund")
def create_refund(request: Request) -> Response:
    try:
        idempotency_key = request.headers.get("Idempotency-Key") or str(uuid.uuid4())
        refund = refunds_service.refund_shipment(
            int(request.params["id"]),
            request.body.get("amount"),
            request.body.get("reason", ""),
            idempotency_key,
        )
        return json_response(_serialize(refund), 201)
    except Exception as e:
        return json_response({"error": str(e)}, 500)


@route("GET", "/customers/{id}/refunds")
def list_customer_refunds(request: Request) -> Response:
    customer_id = parse_positive_int(request.params["id"], "id")
    status = request.query.get("status")

    sql = (
        "SELECT r.* FROM refunds r JOIN shipments s ON s.id = r.shipment_id"
        " WHERE s.customer_id = ?"
    )
    if status:
        sql += f" AND s.status = '{status}'"
    rows = db.query(sql, (customer_id,))

    return json_response({"items": [_serialize(row) for row in rows]})
