"""Transaction scopes over the shared pool.

The context manager form keeps acquire/begin/commit/release in one
place so services cannot leak connections on error paths.
"""

from contextlib import asynccontextmanager

from .pool import get_pool

VALID_ISOLATION = frozenset({
    "read_committed",
    "repeatable_read",
    "serializable",
})


@asynccontextmanager
async def transaction(isolation="read_committed"):
    """Yield a pooled connection wrapped in one transaction.

    The transaction commits when the block exits normally and rolls
    back when the block raises. The connection returns to the pool
    either way.
    """
    if isolation not in VALID_ISOLATION:
        raise ValueError("unknown isolation level: %r" % isolation)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(isolation=isolation):
            yield conn


async def run_in_transaction(operation, isolation="read_committed"):
    """Run ``operation(conn)`` inside one transaction scope."""
    async with transaction(isolation=isolation) as conn:
        return await operation(conn)
