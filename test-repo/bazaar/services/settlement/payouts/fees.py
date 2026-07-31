"""Fee schedule applied to per-seller totals.

Fees are basis points on gross plus a fixed amount per payout, chosen
by the seller's plan.
"""

from services.settlement.models import PayoutLine

# plan -> (basis points, fixed cents per payout)
FEE_SCHEDULE = {
    "standard": (250, 30),
    "plus": (180, 30),
    "enterprise": (120, 0),
}

DEFAULT_PLAN = "standard"


def fee_for(gross_cents, plan):
    """Returns the fee in cents for one payout."""
    bps, fixed_cents = FEE_SCHEDULE.get(plan, FEE_SCHEDULE[DEFAULT_PLAN])
    return gross_cents * bps // 10_000 + fixed_cents


def net_for(gross_cents, plan):
    """Returns gross minus fee, floored at zero."""
    return max(0, gross_cents - fee_for(gross_cents, plan))


def build_payout_lines(totals, day):
    """Turns per-seller totals into payout lines. Runs once nightly."""
    lines = []
    for seller_id, entry in totals.items():
        fee_cents = fee_for(entry["gross_cents"], entry["plan"])
        lines.append(
            PayoutLine(
                seller_id=seller_id,
                gross_cents=entry["gross_cents"],
                fee_cents=fee_cents,
                net_cents=entry["gross_cents"] - fee_cents,
                currency="USD",
                day=day,
            )
        )
    return lines
