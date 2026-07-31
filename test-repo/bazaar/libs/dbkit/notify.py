"""LISTEN/NOTIFY subscriptions for cross-service signals.

Postgres NOTIFY carries small JSON payloads between services that
share the cluster; the promoted-products refresh and flag-file
reload signals travel this way. Callbacks receive the raw payload
text and use ``decode_payload`` to parse it.
"""

import json

from .pool import get_pool


def channel_for(entity):
    """Canonical channel name for an entity kind."""
    return "bazaar_" + entity


async def listen(channel, callback):
    """Attach a callback to a channel; returns the held connection.

    The connection stays out of the pool while the subscription is
    live. Pass it to ``unlisten`` to detach and return it.
    """
    pool = await get_pool()
    conn = await pool.acquire()
    await conn.add_listener(channel, callback)
    return conn


async def unlisten(conn, channel, callback):
    """Detach a callback and return its connection to the pool."""
    await conn.remove_listener(channel, callback)
    pool = await get_pool()
    await pool.release(conn)


def encode_payload(payload):
    """Serialize a small mapping for NOTIFY; the server caps payloads
    at 8000 bytes, so senders keep them to ids and hints."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def decode_payload(payload):
    """Parse a NOTIFY payload; payloads are small JSON documents."""
    if not payload:
        return {}
    return json.loads(payload)
