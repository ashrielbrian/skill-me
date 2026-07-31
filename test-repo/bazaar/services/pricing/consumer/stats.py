"""Latency window for the consumer loop."""

from collections import deque

from services.pricing.config import STATS_WINDOW


class LatencyWindow:
    """Keeps recent per-event latency samples and the current p99."""

    def __init__(self):
        self._window = deque(maxlen=STATS_WINDOW)
        self.p99_ms = 0.0

    def record(self, value):
        """Adds one sample and refreshes the p99. Called once per event."""
        self._window.append(value)
        ordered = sorted(self._window)
        rank = max(0, int(len(ordered) * 0.99) - 1)
        self.p99_ms = ordered[rank]

    def summary(self):
        """Returns the fields reported by the batch log line."""
        if not self._window:
            return {"samples": 0, "p99_ms": 0.0}
        return {"samples": len(self._window), "p99_ms": round(self.p99_ms, 3)}

    def reset(self):
        """Clears the window. Used between test cases."""
        self._window.clear()
        self.p99_ms = 0.0


# One window per worker process, shared by the loop and the log line.
tracker = LatencyWindow()
