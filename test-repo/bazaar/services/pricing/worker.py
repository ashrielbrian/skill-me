"""Pricing worker entry point.

One process per core; each process runs a single consumer loop against
the shared stream group.
"""

import asyncio
import logging

import redis.asyncio as redis

from services.pricing.config import (
    CONSUMER_NAME,
    GROUP_NAME,
    REDIS_HOST,
    REDIS_PORT,
    STREAM_NAME,
)
from services.pricing.consumer.ack import reclaim_stale
from services.pricing.consumer.loop import run_loop

LOG = logging.getLogger(__name__)


def build_client():
    """Creates the process-wide Redis client. Called once at startup."""
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


async def ensure_group(client):
    """Creates the consumer group when it does not exist yet."""
    try:
        await client.xgroup_create(STREAM_NAME, GROUP_NAME, id="$", mkstream=True)
    except redis.ResponseError:
        LOG.info("consumer group %s already exists", GROUP_NAME)


async def main():
    """Boots the worker and runs the loop until the process stops."""
    logging.basicConfig(level=logging.INFO)
    client = build_client()
    await ensure_group(client)
    await reclaim_stale(client, CONSUMER_NAME)
    LOG.info("worker %s consuming stream %s", CONSUMER_NAME, STREAM_NAME)
    try:
        await run_loop(client)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
