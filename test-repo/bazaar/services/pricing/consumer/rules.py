"""Active promo ruleset access."""

import json

from services.pricing.config import PROMO_SNAPSHOT_KEY


async def load_active_promos(redis_client):
    """Returns the active promo ruleset keyed by SKU.

    The snapshot is about 500 KB. Called before each event is applied.
    """
    raw = await redis_client.get(PROMO_SNAPSHOT_KEY)
    if raw is None:
        return {}
    return json.loads(raw)


def rule_applies(rule, region):
    """True when a rule covers the given region.

    Rules with an empty region apply everywhere.
    """
    scoped = rule.get("region", "")
    return scoped == "" or scoped == region


def discount_cents(rule, amount_cents):
    """Returns the discounted amount for one rule, floored at zero."""
    pct_off = rule.get("pct_off", 0)
    reduced = amount_cents * (100 - pct_off) // 100
    return max(0, reduced)
