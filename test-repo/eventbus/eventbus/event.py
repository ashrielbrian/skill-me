"""Event data model."""
from __future__ import annotations
import uuid
import time
from dataclasses import dataclass, field
from typing import Any


# CLEAN — this dataclass is well-designed
@dataclass(frozen=True)
class Event:
    """An immutable event with metadata."""
    topic: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    source: str = ""

    def matches(self, pattern: str) -> bool:
        """Check if this event's topic matches a glob pattern."""
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return self.topic == prefix or self.topic.startswith(prefix + ".")
        return self.topic == pattern


# CLEAN — simple helper
def create_event(topic: str, **kwargs) -> Event:
    """Convenience factory for creating events."""
    return Event(topic=topic, payload=kwargs)
