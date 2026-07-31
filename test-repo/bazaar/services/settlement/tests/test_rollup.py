"""Unit checks for per-seller rollups."""

from services.settlement.config import TOP_SELLERS_LIMIT
from services.settlement.payouts.rollup import (
    order_count,
    rollup_by_seller,
    top_sellers,
)


def _row(seller_id, gross, net):
    return {
        "seller_id": seller_id,
        "gross_cents": gross,
        "net_cents": net,
        "plan": "standard",
    }


def test_rollup_sums_per_seller():
    rows = [_row("a", 100, 90), _row("a", 50, 45), _row("b", 10, 9)]
    totals = rollup_by_seller(rows)
    assert totals["a"]["gross_cents"] == 150
    assert totals["a"]["orders"] == 2
    assert totals["b"]["net_cents"] == 9


def test_rollup_keeps_plan():
    totals = rollup_by_seller([_row("a", 100, 90)])
    assert totals["a"]["plan"] == "standard"


def test_order_count():
    totals = rollup_by_seller([_row("a", 1, 1), _row("b", 2, 2)])
    assert order_count(totals) == 2


def test_top_sellers_ordering_and_limit():
    rows = [_row(str(i), i, i) for i in range(200)]
    leaders = top_sellers(rows)
    assert len(leaders) == TOP_SELLERS_LIMIT
    assert leaders[0]["net_cents"] == 199
    assert leaders[-1]["net_cents"] == 150
