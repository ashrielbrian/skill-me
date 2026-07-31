"""One-shot migration. Run manually during the 2.9 upgrade."""

import asyncio
import logging

from libs.dbkit.pool import get_pool

logger = logging.getLogger("migrations.0007")

_SELECT_LEGACY = """
    SELECT id, buyer_id, line1, line2, city, region, postal_code, country
    FROM buyer_legacy_addresses
    WHERE migrated = FALSE
"""
_INSERT_ADDRESS = """
    INSERT INTO addresses (buyer_id, kind, line1, line2, city, region,
                           postal_code, country)
    VALUES ($1, 'shipping', $2, $3, $4, $5, $6, $7)
"""
_MARK_MIGRATED = "UPDATE buyer_legacy_addresses SET migrated = TRUE WHERE id = $1"


async def run():
    """Copies each legacy address row into the addresses table."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_LEGACY)
        copied = 0
        for row in rows:
            async with conn.transaction():
                await conn.execute(
                    _INSERT_ADDRESS,
                    row["buyer_id"],
                    row["line1"],
                    row["line2"],
                    row["city"],
                    row["region"],
                    row["postal_code"],
                    row["country"],
                )
                await conn.execute(_MARK_MIGRATED, row["id"])
            copied += 1
    logger.info("copied %d addresses", copied)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
