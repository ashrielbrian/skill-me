"""Unit checks for stream entry decoding."""

import pytest

from services.pricing.consumer.decode import to_event
from services.pricing.errors import DecodeError


def _fields(**overrides):
    base = {
        "event_id": "evt-1",
        "sku": "SKU-1",
        "currency": "USD",
        "amount_cents": "1299",
        "region": "eu",
        "category": "digital",
        "type": "price",
        "skus": "SKU-1,SKU-2",
    }
    base.update(overrides)
    return base


def test_routing_key_shape():
    event = to_event(_fields())
    assert event.routing_key == "price.eu.digital.SKU-1"


def test_candidate_skus_split():
    event = to_event(_fields())
    assert event.candidate_skus == ("SKU-1", "SKU-2")


def test_amount_parsed_as_int():
    assert to_event(_fields()).amount_cents == 1299


def test_defaults_when_optional_fields_absent():
    fields = _fields()
    del fields["region"]
    del fields["skus"]
    event = to_event(fields)
    assert event.region == "us"
    assert event.candidate_skus == ("SKU-1",)


def test_missing_required_field_raises():
    fields = _fields()
    del fields["currency"]
    with pytest.raises(DecodeError):
        to_event(fields)
