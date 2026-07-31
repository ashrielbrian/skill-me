"""Buffered producer for Redis streams.

``publish`` queues an event; ``flush`` delivers everything queued
in one pipelined round trip. A flush also runs on its own each
time the buffer reaches ``PUBLISH_FLUSH_COUNT`` entries, and
producers that need a bounded delivery delay call ``flush`` on
their own schedule (request end, batch end, shutdown).
"""

import redis

from . import codec
from .config import PUBLISH_FLUSH_COUNT, REDIS_URL, STREAM_MAXLEN
from .errors import PublishError

_client = None
_buffer = []


def _get_client():
    """Return the process-wide sync Redis client for producers."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL)
    return _client


def publish(stream, event):
    """Queue one event for delivery to a stream."""
    if not isinstance(event, dict):
        raise PublishError("event must be a mapping")
    _buffer.append((stream, codec.encode(event)))
    if len(_buffer) >= PUBLISH_FLUSH_COUNT:
        flush()


def flush():
    """Deliver every queued event in one pipelined round trip."""
    if not _buffer:
        return 0
    pipe = _get_client().pipeline(transaction=False)
    for stream, payload in _buffer:
        pipe.xadd(
            stream,
            {"payload": payload},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    delivered = len(_buffer)
    pipe.execute()
    _buffer.clear()
    return delivered


def pending_count():
    """How many events sit in the buffer awaiting the next flush."""
    return len(_buffer)
