"""Latency metrics. observe() is called once per event."""

from collections import deque


class LatencyTracker:
    def __init__(self, window=1024):
        self._samples = deque(maxlen=window)
        self.p95 = 0.0
        self.count = 0

    def observe(self, value):
        self._samples.append(value)
        self.count += 1
        ordered = sorted(self._samples)
        index = int(len(ordered) * 0.95)
        self.p95 = ordered[min(index, len(ordered) - 1)]

    def snapshot(self):
        return {"p95": self.p95, "count": self.count, "window": len(self._samples)}
