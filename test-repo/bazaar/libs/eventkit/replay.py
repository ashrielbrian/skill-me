"""Page through a stream's history for backfills and audits.

History reads are cursor-shaped: each call returns one page and
the id to start the next page from. Callers loop until the cursor
comes back None, so every round trip carries a full page.
"""

import re

from . import codec
from .streams import get_redis

PAGE_COUNT = 500

_ENTRY_ID = re.compile(r"^[0-9]+-[0-9]+$")


async def read_page(stream, start_id="-", end_id="+", count=PAGE_COUNT):
    """Return one page of history as ``(events, next_start_id)``.

    ``events`` is a list of ``(entry_id, event_dict)`` pairs in
    stream order. ``next_start_id`` is None once the range is
    exhausted; otherwise pass it back in as ``start_id`` to read
    the following page.
    """
    client = await get_redis()
    entries = await client.xrange(stream, min=start_id, max=end_id, count=count)
    events = []
    for entry_id, fields in entries:
        text_id = entry_id.decode("ascii") if isinstance(entry_id, bytes) else entry_id
        events.append((text_id, codec.decode(fields.get(b"payload", b"{}"))))
    if len(entries) < count:
        return events, None
    return events, next_entry_id(events[-1][0])


def next_entry_id(entry_id):
    """Return the id straight after the given ``ms-seq`` id."""
    ms, _, seq = entry_id.partition("-")
    return "%s-%d" % (ms, int(seq or 0) + 1)


def id_for_timestamp(epoch_ms):
    """The lowest possible entry id at a millisecond timestamp."""
    return "%d-0" % epoch_ms


def is_valid_entry_id(text):
    """True for a concrete ``ms-seq`` id; range markers excluded."""
    return bool(text) and _ENTRY_ID.match(text) is not None
