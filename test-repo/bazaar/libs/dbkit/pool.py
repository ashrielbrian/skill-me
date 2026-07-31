"""Process-wide asyncpg connection pool.

Each service process owns exactly one pool for its whole lifetime.
``get_pool`` creates it on first use and every later call returns
the same object; ``close_pool`` exists for orderly shutdown hooks.
Acquiring from the pool is how every other dbkit module reaches
the database.
"""

import asyncio
import logging

import asyncpg

from .config import (
    APPLICATION_NAME,
    CONNECT_TIMEOUT_S,
    DSN,
    POOL_MAX,
    POOL_MIN,
    STATEMENT_TIMEOUT_MS,
)

logger = logging.getLogger("bazaar.dbkit.pool")

_pool = None
_pool_lock = asyncio.Lock()


async def get_pool():
    """Return the singleton pool, creating it on first call."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        # Re-check inside the lock; another task may have won the race.
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn=DSN,
                min_size=POOL_MIN,
                max_size=POOL_MAX,
                timeout=CONNECT_TIMEOUT_S,
                server_settings={
                    "application_name": APPLICATION_NAME,
                    "statement_timeout": str(STATEMENT_TIMEOUT_MS),
                },
            )
            logger.info("pool ready min=%d max=%d", POOL_MIN, POOL_MAX)
    return _pool


async def close_pool():
    """Close the singleton pool during process shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("pool closed")
