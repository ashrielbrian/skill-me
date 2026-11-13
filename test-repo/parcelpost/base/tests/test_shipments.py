from parcelpost.http import Request, dispatch
from parcelpost.services import shipments as shipments_service


def _create(customer_id=1, **overrides):
    body = {"customer_id": customer_id, "carrier": "ups", "weight_g": 500, "dest_country": "de"}
    body.update(overrides)
    return dispatch(Request("POST", "/shipments", body=body))


def test_create_shipment():
    response = _create(declared_value="19.99")
    assert response.status == 201
    assert response.body["charge_cents"] == 1999
    assert response.body["declared_cents"] == 1999
    assert response.body["dest_country"] == "DE"
    assert response.body["status"] == "labelled"


def test_create_shipment_validates():
    assert _create(weight_g=-1).status == 422
    assert _create(dest_country="germany").status == 422
    assert _create(declared_value=19.99).status == 422


def test_get_missing_shipment_is_404():
    response = dispatch(Request("GET", "/shipments/99"))
    assert response.status == 404
    assert response.body["error"]["code"] == "not_found"


def test_pagination_walks_every_shipment_once():
    for _ in range(5):
        _create()
    _create(customer_id=2)

    seen, cursor = [], 0
    while cursor is not None:
        page = shipments_service.list_shipments(1, limit=2, after_id=cursor)
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]

    assert seen == [1, 2, 3, 4, 5]
