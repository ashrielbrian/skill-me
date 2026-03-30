"""Logging middleware."""
import logging
import json
from ..event import Event

logger = logging.getLogger(__name__)


class LoggingMiddleware:
    """Logs all events passing through the bus."""

    def __init__(self, level: int = logging.INFO, include_payload: bool = True):
        self.level = level
        self.include_payload = include_payload

    async def __call__(self, event: Event) -> Event:
        """Log the event and pass it through."""
        log_data = {
            "event_id": event.event_id,
            "topic": event.topic,
            "source": event.source,
            "timestamp": event.timestamp,
        }
        if self.include_payload:
            # BUG [SECURITY]: Logs full event payload which may contain PII,
            # credentials, or other sensitive data. The default is include_payload=True,
            # so this fires for every event unless explicitly disabled.
            log_data["payload"] = event.payload

        logger.log(self.level, json.dumps(log_data))
        return event
