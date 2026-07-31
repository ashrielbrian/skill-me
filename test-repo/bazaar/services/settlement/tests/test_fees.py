"""Unit checks for the fee schedule."""

from datetime import date

from services.settlement.payouts.fees import build_payout_lines, fee_for, net_for


def test_standard_plan_fee():
    # 250 bps of 10,000 plus 30 fixed.
    assert fee_for(10_000, "standard") == 280


def test_plus_plan_fee():
    assert fee_for(10_000, "plus") == 210


def test_enterprise_has_no_fixed_part():
    assert fee_for(10_000, "enterprise") == 120


def test_unknown_plan_uses_default():
    assert fee_for(10_000, "mystery") == fee_for(10_000, "standard")


def test_net_never_below_zero():
    assert net_for(5, "standard") == 0


def test_build_payout_lines_net():
    totals = {
        "seller-1": {
            "gross_cents": 10_000,
            "net_cents": 0,
            "orders": 3,
            "plan": "standard",
        }
    }
    lines = build_payout_lines(totals, date(2026, 7, 30))
    assert len(lines) == 1
    assert lines[0].seller_id == "seller-1"
    assert lines[0].fee_cents == 280
    assert lines[0].net_cents == 9_720
