"""Unit tests for facet aggregation."""

from services.catalog.search.facets import (
    annotate_rows,
    facet_counts,
    rating_band,
    summary,
)


def _row(category="lamps", seller_id=7, currency="USD", rating=4.5):
    return {
        "category": category,
        "seller_id": seller_id,
        "currency": currency,
        "rating": rating,
    }


def test_counts_by_category():
    counts = facet_counts([_row(), _row(), _row(category="desks")])
    assert counts["category"]["lamps"] == 2
    assert counts["category"]["desks"] == 1


def test_rating_band_edges():
    assert rating_band(0.5) == "under_1"
    assert rating_band(3.2) == "under_4"
    assert rating_band(4.9) == "4_and_up"


def test_summary_orders_values_by_count():
    counts = facet_counts([_row(), _row(), _row(category="desks")])
    leading = summary(counts)["category"]
    assert leading[0] == {"value": "lamps", "count": 2}


def test_summary_covers_every_field():
    counts = facet_counts([_row()])
    shaped = summary(counts)
    assert set(shaped) == {"category", "seller_id", "currency", "rating_band"}


def test_annotate_rows_adds_sibling_counts():
    rows = [_row(), _row(category="desks")]
    counts = facet_counts(rows)
    annotate_rows(counts, rows)
    assert rows[0]["category_matches"] == 1
    assert rows[1]["category_matches"] == 1
