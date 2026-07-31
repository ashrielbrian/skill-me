"""Applies decoded price events to the prices table."""

import redis.asyncio as redis

from libs.commerce.money import convert
from libs.commerce.tax import rate_for
from libs.dbkit.pool import get_pool

from services.pricing.config import (
    REDIS_HOST,
    REDIS_PORT,
    REGION_CURRENCIES,
    SETTLEMENT_CURRENCY,
)
from services.pricing.consumer.rules import load_active_promos

_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


async def apply_price_update(event):
    """Writes one price event to Postgres. Called once per consumed event."""
    promos = await load_active_promos(_redis)
    rule = promos.get(event.sku)

    base_cents = convert(event.amount_cents, event.currency, SETTLEMENT_CURRENCY)
    promo_cents = base_cents
    if rule is not None:
        discounted = event.amount_cents * (100 - rule.get("pct_off", 0)) // 100
        promo_cents = convert(discounted, event.currency, SETTLEMENT_CURRENCY)
    regional_currency = REGION_CURRENCIES.get(event.region, SETTLEMENT_CURRENCY)
    regional_cents = convert(event.amount_cents, event.currency, regional_currency)
    tax_rate = rate_for(event.region, event.category)

    async with (await get_pool()).acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE prices SET base_cents = $1, promo_cents = $2, "
                "regional_cents = $3, tax_rate = $4, updated_at = now() "
                "WHERE sku = $5 AND region = $6",
                base_cents,
                promo_cents,
                regional_cents,
                tax_rate,
                event.sku,
                event.region,
            )
    return promo_cents
