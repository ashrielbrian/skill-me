"""Deduplication middleware."""
import time
from ..event import Event


class DeduplicationMiddleware:
    """Filters duplicate events within a time window."""

    def __init__(self, window_seconds: float = 60.0, max_seen: int = 10000):
        self._seen: dict[str, float] = {}
        self.window_seconds = window_seconds
        self.max_seen = max_seen

    async def __call__(self, event: Event) -> Event | None:
        """Filter if event_id was seen recently."""
        self._cleanup_expired()

        if event.event_id in self._seen:
            return None  # Duplicate — filter it

        self._seen[event.event_id] = time.time()
        return event

    def _cleanup_expired(self):
        """Remove expired entries."""
        now = time.time()
        # BUG [PERFORMANCE]: Iterates ALL entries every time to find expired ones.
        # With max_seen=10000 and frequent events, this is O(n) per event.
        # Should use an ordered structure (e.g., deque with timestamps) for O(1) cleanup.
        expired = [k for k, ts in self._seen.items()
                   if now - ts > self.window_seconds]
        for k in expired:
            del self._seen[k]

        # BUG [CORRECTNESS]: When max_seen is exceeded, drops the NEWEST entries
        # (dict iteration order is insertion order, so list()[:excess] gets oldest,
        # but the logic below drops from the end). Actually wait — let me re-examine.
        if len(self._seen) > self.max_seen:
            excess = len(self._seen) - self.max_seen
            # This removes the OLDEST entries (first inserted), which is correct.
            # The comment above about dropping newest is wrong — this is actually fine.
            for k in list(self._seen.keys())[:excess]:
                del self._seen[k]
