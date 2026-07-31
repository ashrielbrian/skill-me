"""Unit checks for campaign SKU matching."""

from types import SimpleNamespace

from services.pricing.consumer.match import (
    find_affected_skus,
    normalize_sku,
    primary_first,
)


def _event(skus, primary=None):
    return SimpleNamespace(
        candidate_skus=tuple(skus),
        sku=primary or (skus[0] if skus else ""),
    )


def test_finds_only_campaign_skus():
    event = _event(["SKU-1", "SKU-2", "SKU-3"])
    roster = ["SKU-2", "SKU-9"]
    assert find_affected_skus(event, roster) == ["SKU-2"]


def test_empty_event_matches_nothing():
    assert find_affected_skus(_event([]), ["SKU-1"]) == []


def test_empty_roster_matches_nothing():
    assert find_affected_skus(_event(["SKU-1"]), []) == []


def test_normalize_sku():
    assert normalize_sku(" sku-1 ") == "SKU-1"


def test_primary_first_reorders():
    event = _event(["SKU-2", "SKU-1"], primary="SKU-1")
    assert primary_first(event) == ("SKU-1", "SKU-2")
