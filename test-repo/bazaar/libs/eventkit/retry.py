"""Redelivery policy for events that fail handling.

Failed events move to a per-stream retry stream with an advanced
attempt counter, and to the dead-letter stream once attempts run
out. The delay schedule spreads repeats so a struggling handler
is not hit again right away.
"""

from . import codec
from .streams import get_redis

MAX_ATTEMPTS = 5

# Pause before each redelivery, indexed by attempt number.
DELAY_SCHEDULE_MS = (0, 1000, 5000, 30000, 120000)


def attempt_of(event):
    """The attempt counter carried in the event envelope."""
    return int(event.get("attempt", 0))


def should_retry(event):
    """True while the event has redelivery attempts left."""
    return attempt_of(event) + 1 < MAX_ATTEMPTS


def with_next_attempt(event):
    """Copy the event with its attempt counter advanced."""
    advanced = dict(event)
    advanced["attempt"] = attempt_of(event) + 1
    return advanced


def delay_for(event):
    """Milliseconds to wait before redelivering this event."""
    index = min(attempt_of(event), len(DELAY_SCHEDULE_MS) - 1)
    return DELAY_SCHEDULE_MS[index]


def retry_stream(stream):
    """Companion stream that carries repeats for a stream."""
    return stream + ":retry"


def dead_letter_stream(stream):
    """Companion stream that carries events out of attempts."""
    return stream + ":dead"


async def requeue(stream, event):
    """Queue one failed event on its retry or dead-letter stream."""
    client = await get_redis()
    if should_retry(event):
        target = retry_stream(stream)
        body = with_next_attempt(event)
    else:
        target = dead_letter_stream(stream)
        body = event
    entry_id = await client.xadd(target, {"payload": codec.encode(body)})
    return target, entry_id
