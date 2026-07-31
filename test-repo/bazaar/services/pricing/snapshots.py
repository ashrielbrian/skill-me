"""Promo ruleset snapshot publisher.

Builds the ruleset from Postgres and writes it to Redis under
PROMO_SNAPSHOT_KEY. Run hourly by the admin CLI, never by workers.
"""

import json
import logging

from libs.dbkit.executor import execute, fetch_all, fetch_one

from services.pricing.config import PROMO_SNAPSHOT_KEY

LOG = logging.getLogger(__name__)

RULES_QUERY = (
    "SELECT sku, pct_off, code, region FROM promo_rules "
    "WHERE active AND now() BETWEEN starts_at AND ends_at"
)

STAMP_QUERY = "UPDATE promo_meta SET published_at = now() WHERE id = 1"

AGE_QUERY = (
    "SELECT extract(epoch FROM now() - published_at) AS age "
    "FROM promo_meta WHERE id = 1"
)


async def snapshot_age_seconds():
    """Returns seconds since the last publish. Used by the health check."""
    row = await fetch_one(AGE_QUERY)
    if row is None:
        return None
    return float(row["age"])


async def build_ruleset():
    """Assembles the ruleset keyed by SKU. Called once per publish."""
    rows = await fetch_all(RULES_QUERY)
    rules = {}
    for row in rows:
        rules[row["sku"]] = {
            "pct_off": row["pct_off"],
            "code": row["code"],
            "region": row["region"],
        }
    return rules


async def publish_snapshot(redis_client):
    """Serializes the ruleset and writes it to Redis. Runs hourly."""
    rules = await build_ruleset()
    payload = json.dumps(rules, separators=(",", ":"))
    await redis_client.set(PROMO_SNAPSHOT_KEY, payload)
    await execute(STAMP_QUERY)
    LOG.info("published %d promo rules (%d bytes)", len(rules), len(payload))
    return len(rules)
