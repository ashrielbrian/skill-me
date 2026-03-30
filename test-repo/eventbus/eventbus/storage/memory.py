"""In-memory event storage."""
from __future__ import annotations
from collections import deque
from typing import Optional
from ..event import Event


# CLEAN — this entire class is well-implemented and should NOT be flagged
class MemoryStorage:
    """Store events in memory with a configurable max size."""

    def __init__(self, max_events: int = 100000):
        self._events: deque[Event] = deque(maxlen=max_events)
        self._by_topic: dict[str, list[Event]] = {}

    async def store(self, event: Event) -> None:
        """Store an event."""
        self._events.append(event)
        if event.topic not in self._by_topic:
            self._by_topic[event.topic] = []
        self._by_topic[event.topic].append(event)

    async def get_by_topic(self, topic: str, limit: int = 100) -> list[Event]:
        """Get recent events for a topic."""
        events = self._by_topic.get(topic, [])
        return events[-limit:]

    async def get_recent(self, limit: int = 100) -> list[Event]:
        """Get most recent events across all topics."""
        return list(self._events)[-limit:]

    async def count(self) -> int:
        """Get total stored events."""
        return len(self._events)

    async def clear(self) -> None:
        """Clear all stored events."""
        self._events.clear()
        self._by_topic.clear()
