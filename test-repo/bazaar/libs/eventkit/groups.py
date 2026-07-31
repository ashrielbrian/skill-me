"""Consumer-group lifecycle helpers.

Each service creates its groups at startup, then its workers read
with a stable per-process consumer name so pending entries can be
traced back to the process that took them.
"""

import os
import socket

from redis import exceptions as redis_exceptions

from .streams import get_redis


def consumer_name():
    """Stable per-process consumer identity within the group."""
    return "%s-%d" % (socket.gethostname(), os.getpid())


async def ensure_group(stream, group):
    """Create the group at the stream tail if it does not exist.

    Returns True when this call created the group and False when it
    already existed; both outcomes leave the group ready for reads.
    """
    client = await get_redis()
    try:
        await client.xgroup_create(stream, group, id="$", mkstream=True)
        return True
    except redis_exceptions.ResponseError as exc:
        # BUSYGROUP means another worker created it first; that is fine.
        if "BUSYGROUP" not in str(exc):
            raise
        return False


async def remove_consumer(stream, group, name):
    """Remove a consumer from a group.

    The server discards the consumer's pending entries, so callers
    reclaim through ``ack.claim_idle`` before removing.
    """
    client = await get_redis()
    return await client.xgroup_delconsumer(stream, group, name)


async def group_summary(stream):
    """Return the server's group listing for one stream."""
    client = await get_redis()
    return await client.xinfo_groups(stream)
