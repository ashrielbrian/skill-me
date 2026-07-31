"""Risk holdbacks withheld from seller payouts.

The withheld share depends on the seller's risk tier and is released
on a later settlement day. Used by the payout build in tools/ during
tier migrations.
"""

from datetime import timedelta

# tier -> basis points withheld from net
HOLDBACK_BPS = {
    "low": 0,
    "standard": 100,
    "elevated": 500,
}

# tier -> days until the withheld share is payable
RELEASE_DELAY_DAYS = {
    "low": 0,
    "standard": 7,
    "elevated": 30,
}

DEFAULT_TIER = "standard"


def holdback_cents(net_cents, tier):
    """Returns the withheld portion of one payout."""
    bps = HOLDBACK_BPS.get(tier, HOLDBACK_BPS[DEFAULT_TIER])
    return net_cents * bps // 10_000


def release_day(day, tier):
    """Returns the day a holdback becomes payable."""
    delay = RELEASE_DELAY_DAYS.get(tier, RELEASE_DELAY_DAYS[DEFAULT_TIER])
    return day + timedelta(days=delay)


def split_payout(net_cents, tier):
    """Returns (payable_now, held) for one payout line."""
    held = holdback_cents(net_cents, tier)
    return net_cents - held, held
