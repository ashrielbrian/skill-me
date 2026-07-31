"""Consumer liveness markers with expiry.

Each worker refreshes a keyed marker between batches; operators
and the reclaim job read the markers to tell a busy consumer from
a dead one. A marker that lapses past its TTL simply disappears,
so absence is the signal.
"""

from datetime import datetime, timezone

from .streams import get_redis

KEY_PREFIX = "bazaar:consumer:alive:"

# Marker lifetime; a worker quiet this long counts as gone.
TTL_S = 30


def key_for(name):
    """Redis key holding the liveness marker for one consumer."""
    return KEY_PREFIX + name


async def beat(name):
    """Refresh the named consumer's marker and return the stamp."""
    client = await get_redis()
    stamp = datetime.now(timezone.utc).isoformat()
    await client.set(key_for(name), stamp, ex=TTL_S)
    return stamp


async def is_alive(name):
    """True when the named consumer refreshed its marker recently."""
    client = await get_redis()
    return bool(await client.exists(key_for(name)))


async def last_seen(name):
    """Return the consumer's last marker value, or None."""
    client = await get_redis()
    value = await client.get(key_for(name))
    if isinstance(value, bytes):
        return value.decode("ascii")
    return value
