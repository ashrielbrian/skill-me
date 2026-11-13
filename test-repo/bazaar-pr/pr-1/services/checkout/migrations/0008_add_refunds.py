"""Adds the refunds table and the order status index.

Applied once during the 2.12 release, before the refund endpoint ships.
"""

import asyncio
import logging

from libs.dbkit.pool import get_pool

logger = logging.getLogger("migrations.0008")

_CREATE_REFUNDS = """
    CREATE TABLE refunds (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL REFERENCES orders (id),
        amount_cents BIGINT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""
_CREATE_STATUS_INDEX = """
    CREATE INDEX orders_status_updated_idx ON orders (status, updated_at)
"""


async def run():
    """Applies the schema changes in one transaction."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_CREATE_REFUNDS)
            await conn.execute(_CREATE_STATUS_INDEX)
    logger.info("refunds table created")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
