from parcelpost import db
from parcelpost.errors import UpstreamError
from parcelpost.http import Request, dispatch

ITEM = {"carrier": "ups", "weight_g": 500, "dest_country": "de"}


def _bulk(items, customer_id=1):
    body = {"customer_id": customer_id, "items": items}
    return dispatch(Request("POST", "/shipments/bulk", body=body))


def test_bulk_creates_every_shipment():
    response = _bulk([ITEM, {**ITEM, "weight_g": 900, "declared_value": "12.50"}])
    assert response.status == 201
    first, second = response.body["items"]
    assert (first["id"], second["id"]) == (1, 2)
    assert first["label_id"] != second["label_id"]
    assert second["declared_cents"] == 1250
    assert second["customer_id"] == 1


def test_bulk_validates_each_item():
    response = _bulk([ITEM, {**ITEM, "dest_country": "germany"}])
    assert response.status == 422
    assert db.query("SELECT * FROM shipments") == []


def test_bulk_is_all_or_nothing(fake_carrier, monkeypatch):
    def get_rate(carrier, weight_g, dest_country):
        if weight_g == 999:
            raise UpstreamError("carrier down")
        return 1999

    monkeypatch.setattr(fake_carrier, "get_rate", get_rate)
    monkeypatch.setattr("parcelpost.retry.random.uniform", lambda a, b: 0)

    response = _bulk([ITEM, ITEM, {**ITEM, "weight_g": 999}])

    assert response.status == 502
    assert db.query("SELECT * FROM shipments") == []
