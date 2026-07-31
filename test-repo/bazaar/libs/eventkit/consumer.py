"""Consumer-group reads from Redis streams.

Workers call ``consume`` in their loops, handle what comes back,
then ack the entry ids through ``ack.ack``. Group creation is a
separate startup step; see ``groups.ensure_group``.
"""

from . import codec
from .config import CONSUMER_BLOCK_MS, CONSUMER_GROUP
from .groups import consumer_name
from .streams import get_redis


async def consume(stream, count):
    """Read up to ``count`` new entries for this process's consumer.

    Returns a list of ``(entry_id, event_dict)`` pairs. The list is
    empty when the block window passes with nothing new, which lets
    worker loops stay responsive to shutdown between reads.
    """
    client = await get_redis()
    reply = await client.xreadgroup(
        groupname=CONSUMER_GROUP,
        consumername=consumer_name(),
        streams={stream: ">"},
        count=count,
        block=CONSUMER_BLOCK_MS,
    )
    events = []
    if reply:
        # Reply shape: [(stream_key, [(entry_id, fields), ...]), ...]
        for _key, entries in reply:
            for entry_id, fields in entries:
                payload = fields.get(b"payload", b"{}")
                events.append((_as_text(entry_id), codec.decode(payload)))
    return events


def _as_text(value):
    """Entry ids arrive as bytes from the client."""
    return value.decode("ascii") if isinstance(value, bytes) else value
