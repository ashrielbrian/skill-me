"""Adds the webhook subscription and delivery tables.

Applied once during the 2.8 release, before the webhook endpoints ship.
"""

import asyncio
import logging

from libs.dbkit.pool import get_pool

logger = logging.getLogger("migrations.0006")

_CREATE_SUBSCRIPTIONS = """
    CREATE TABLE webhook_subscriptions (
        id BIGSERIAL PRIMARY KEY,
        topic TEXT NOT NULL,
        url TEXT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""
_CREATE_TOPIC_INDEX = """
    CREATE INDEX webhook_subscriptions_topic_idx
    ON webhook_subscriptions (topic) WHERE active
"""
_CREATE_DELIVERIES = """
    CREATE TABLE webhook_deliveries (
        id BIGSERIAL PRIMARY KEY,
        subscription_id BIGINT NOT NULL REFERENCES webhook_subscriptions (id),
        order_id TEXT NOT NULL,
        status_code INTEGER,
        attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""


async def run():
    """Applies the schema changes in one transaction."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_CREATE_SUBSCRIPTIONS)
            await conn.execute(_CREATE_TOPIC_INDEX)
            await conn.execute(_CREATE_DELIVERIES)
    logger.info("webhook tables created")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
