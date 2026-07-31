"""Acknowledgement helpers for the pricing stream group."""

import logging

from services.pricing.config import GROUP_NAME, STREAM_NAME

LOG = logging.getLogger(__name__)

# Entries idle beyond this are taken over by a starting worker.
STALE_IDLE_MS = 60_000


async def ack_batch(redis_client, stream, group, entry_ids):
    """Acknowledges one batch of entries with a single XACK call."""
    if not entry_ids:
        return 0
    return await redis_client.xack(stream, group, *entry_ids)


async def reclaim_stale(redis_client, consumer_name):
    """Claims entries idle past STALE_IDLE_MS. Runs when a worker starts."""
    claimed = await redis_client.xautoclaim(
        STREAM_NAME,
        GROUP_NAME,
        consumer_name,
        min_idle_time=STALE_IDLE_MS,
        start_id="0-0",
    )
    entries = claimed[1] if claimed else []
    if entries:
        LOG.info("reclaimed %d stale entries", len(entries))
    return entries
