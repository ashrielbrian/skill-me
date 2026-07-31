"""Redis-backed cache for product detail payloads.

Detail traffic runs about 500 req/s; entries expire after
DETAIL_CACHE_TTL_S seconds so price edits surface within a minute.
"""

import json

import redis.asyncio as redis

from services.catalog import config

# One client per process; every handler shares its connection pool.
_client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)


def client() -> redis.Redis:
    """Returns the process-wide Redis client."""
    return _client


def detail_key(product_id: int) -> str:
    """Key layout: catalog:detail:<id>."""
    return "catalog:detail:%d" % product_id


async def get_detail(product_id: int):
    """Reads one cached detail payload. Runs once per detail request."""
    raw = await _client.get(detail_key(product_id))
    if raw is None:
        return None
    return json.loads(raw)


async def set_detail(product_id: int, payload: dict) -> None:
    """Stores a detail payload with the standard TTL."""
    await _client.set(
        detail_key(product_id),
        json.dumps(payload, separators=(",", ":")),
        ex=config.DETAIL_CACHE_TTL_S,
    )


async def drop_detail(product_id: int) -> None:
    """Removes one entry; the admin CLI calls this after manual edits."""
    await _client.delete(detail_key(product_id))
