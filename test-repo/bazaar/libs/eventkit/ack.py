"""Acknowledgement and reclaim helpers.

Entries stay pending until acked, so a worker that dies mid-batch
leaves its entries claimable by a sibling through ``claim_idle``.
"""

from .groups import consumer_name
from .streams import get_redis


async def ack(stream, group, entry_ids):
    """Acknowledge handled entries in one call; returns the count."""
    if not entry_ids:
        return 0
    client = await get_redis()
    return await client.xack(stream, group, *entry_ids)


async def claim_idle(stream, group, min_idle_ms=60000, count=100):
    """Claim entries whose previous consumer went quiet.

    Entries idle longer than ``min_idle_ms`` move to this process's
    consumer so they get handled instead of sitting pending forever.
    """
    client = await get_redis()
    reply = await client.xautoclaim(
        stream,
        group,
        consumer_name(),
        min_idle_time=min_idle_ms,
        count=count,
    )
    # xautoclaim returns (next_start_id, entries, deleted_ids).
    return reply[1]


async def pending_summary(stream, group):
    """Return the server's pending-entry overview for one group."""
    client = await get_redis()
    return await client.xpending(stream, group)
