"""In-process counters for the event transport.

The counter set is fixed at import so the store never grows; each
service exposes ``collect`` through its own metrics endpoint.
"""

import threading

_lock = threading.Lock()
_counts = {
    "published": 0,
    "flushed": 0,
    "consumed": 0,
    "acked": 0,
    "requeued": 0,
    "dead_lettered": 0,
    "decode_errors": 0,
}


def _bump(name, amount):
    with _lock:
        _counts[name] += amount


def note_published(amount=1):
    """Count events queued by the producer."""
    _bump("published", amount)


def note_flushed(amount=1):
    """Count events delivered by a flush."""
    _bump("flushed", amount)


def note_consumed(amount=1):
    """Count events handed to handlers."""
    _bump("consumed", amount)


def note_acked(amount=1):
    """Count entries acknowledged."""
    _bump("acked", amount)


def note_requeued(amount=1):
    """Count events sent to a retry stream."""
    _bump("requeued", amount)


def note_dead_lettered(amount=1):
    """Count events that ran out of attempts."""
    _bump("dead_lettered", amount)


def note_decode_error(amount=1):
    """Count payloads the codec rejected."""
    _bump("decode_errors", amount)


def collect():
    """Copy out the counter values for reporting."""
    with _lock:
        return dict(_counts)


def reset():
    """Zero every counter; used by tests."""
    with _lock:
        for name in _counts:
            _counts[name] = 0
