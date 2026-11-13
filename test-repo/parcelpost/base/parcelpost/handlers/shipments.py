from ..http import Request, Response, json_response, route
from ..money import to_cents
from ..services import shipments as shipments_service
from ..validation import parse_country, parse_limit, parse_positive_int, require_fields


@route("POST", "/shipments")
def create_shipment(request: Request) -> Response:
    body = request.body
    require_fields(body, "customer_id", "carrier", "weight_g", "dest_country")
    declared = body.get("declared_value")
    shipment = shipments_service.create_shipment(
        parse_positive_int(body["customer_id"], "customer_id"),
        body["carrier"],
        parse_positive_int(body["weight_g"], "weight_g"),
        parse_country(body["dest_country"]),
        to_cents(declared, "declared_value") if declared is not None else None,
    )
    return json_response(shipment, 201)


@route("GET", "/shipments/{id}")
def get_shipment(request: Request) -> Response:
    shipment_id = parse_positive_int(request.params["id"], "id")
    return json_response(shipments_service.get_shipment(shipment_id))


@route("GET", "/customers/{id}/shipments")
def list_customer_shipments(request: Request) -> Response:
    customer_id = parse_positive_int(request.params["id"], "id")
    after_id = int(request.query.get("after", 0))
    page = shipments_service.list_shipments(customer_id, parse_limit(request.query), after_id)
    return json_response(page)
