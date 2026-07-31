"""In-process counters and timings for database calls.

Each service exposes these through its own metrics endpoint. The
store is process-local and keyed by the fixed names callers pass,
so it stays small and needs no eviction.
"""

import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_counters = {}
_timings = {}


def incr(name, amount=1):
    """Add to a named counter."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def observe(name, elapsed_ms):
    """Record one duration under a named timing series."""
    with _lock:
        entry = _timings.setdefault(name, [0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += elapsed_ms
        entry[2] = max(entry[2], elapsed_ms)


@contextmanager
def timed(name):
    """Time a block and record it under ``name``."""
    started = time.monotonic()
    try:
        yield
    finally:
        observe(name, (time.monotonic() - started) * 1000.0)


def collect():
    """Copy out counters and timing summaries for reporting."""
    with _lock:
        counters = dict(_counters)
        timings = {
            name: {"count": entry[0], "total_ms": entry[1], "max_ms": entry[2]}
            for name, entry in _timings.items()
        }
    return {"counters": counters, "timings": timings}


def reset():
    """Clear all recorded values; used by tests."""
    with _lock:
        _counters.clear()
        _timings.clear()
