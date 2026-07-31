"""Stream naming and the shared async Redis client.

Stream keys follow ``prefix:domain:entity`` so operators can group
them at a glance. The async client is one per process, created on
first use, and shared by the consumer-side modules.
"""

from redis import asyncio as aioredis

from .config import REDIS_URL, STREAM_PREFIX

_client = None


async def get_redis():
    """Return the process-wide async Redis client."""
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=False)
    return _client


async def close_redis():
    """Close the shared client during process shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# Producing domains registered on the platform.
KNOWN_DOMAINS = frozenset({
    "catalog", "checkout", "inventory", "pricing", "settlement",
})


def is_known_domain(domain):
    """True when the domain is registered above."""
    return domain in KNOWN_DOMAINS


def stream_name(domain, entity):
    """Compose the canonical stream key, e.g. ``bazaar:pricing:promo``."""
    return "%s:%s:%s" % (STREAM_PREFIX, domain, entity)


def parse_stream(key):
    """Split a stream key back into ``(domain, entity)``."""
    prefix, _, remainder = key.partition(":")
    if prefix != STREAM_PREFIX or ":" not in remainder:
        raise ValueError("not a bazaar stream key: %r" % key)
    domain, _, entity = remainder.partition(":")
    return domain, entity
