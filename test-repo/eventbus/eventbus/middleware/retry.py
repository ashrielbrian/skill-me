"""Retry middleware — wraps handlers with retry logic."""
import asyncio
import logging
from typing import Callable, Awaitable
from ..event import Event

logger = logging.getLogger(__name__)


class RetryMiddleware:
    """Middleware that wraps event delivery with retry logic.

    Note: this modifies handler behavior, not the event itself.
    It's registered as a middleware but actually wraps the dispatch phase.
    """

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._retry_counts: dict[str, int] = {}

    async def __call__(self, event: Event) -> Event:
        """Pass through — retry logic is applied at handler dispatch time."""
        # Track that this event entered the retry middleware
        self._retry_counts[event.event_id] = 0
        return event

    async def with_retry(self, handler: Callable[[Event], Awaitable[None]],
                         event: Event) -> None:
        """Execute a handler with exponential backoff retry."""
        for attempt in range(self.max_retries + 1):
            try:
                await handler(event)
                return
            except Exception as e:
                if attempt == self.max_retries:
                    logger.error(f"Handler failed after {self.max_retries} retries: {e}")
                    raise
                delay = self.base_delay * (2 ** attempt)
                logger.warning(f"Retry {attempt + 1}/{self.max_retries} after {delay}s: {e}")
                await asyncio.sleep(delay)


# CLEAN — the retry logic above is correctly implemented with exponential backoff
