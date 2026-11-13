from unittest.mock import MagicMock, patch

from parcelpost.services import refunds as refunds_service
from parcelpost.services.refund_strategy import StandardRefundStrategy, get_strategy

SHIPMENT = {"id": 1, "carrier": "ups", "label_id": "LBL-1", "charge_cents": 1999}


@patch("parcelpost.services.refunds.get_client")
@patch("parcelpost.services.refunds.shipments_repo")
@patch("parcelpost.services.refunds.refunds_repo")
def test_refund_shipment(refunds_repo, shipments_repo, get_client):
    refunds_repo.get_by_idempotency_key.return_value = None
    refunds_repo.insert.return_value = 7
    shipments_repo.get.return_value = SHIPMENT

    refunds_service.refund_shipment(1, None, "damaged", "key-1")

    refunds_repo.insert.assert_called_once_with(1, 1999, "damaged", "key-1")
    shipments_repo.set_status.assert_called_once_with(1, "refunded")
    get_client.return_value.void_label.assert_called_once_with("ups", "LBL-1")


@patch("parcelpost.services.refunds.get_client")
@patch("parcelpost.services.refunds.shipments_repo")
@patch("parcelpost.services.refunds.refunds_repo")
def test_refund_is_idempotent(refunds_repo, shipments_repo, get_client):
    refunds_repo.get_by_idempotency_key.return_value = {"id": 7}

    result = refunds_service.refund_shipment(1, None, "damaged", "key-1")

    assert result == {"id": 7}
    refunds_repo.insert.assert_not_called()


def test_partial_refund_amount():
    strategy = StandardRefundStrategy()
    assert strategy.compute(SHIPMENT, 500) == 500
    assert strategy.compute(SHIPMENT, None) == 1999


def test_get_strategy():
    assert isinstance(get_strategy("standard"), StandardRefundStrategy)
    assert MagicMock() is not None
