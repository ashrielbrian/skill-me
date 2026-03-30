"""Core event bus implementation."""
from __future__ import annotations
import asyncio
import logging
from collections import defaultdict
from typing import Callable, Awaitable, Any

from .event import Event

logger = logging.getLogger(__name__)

# Type alias for handlers
Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Async event bus with topic-based pub/sub."""

    def __init__(self, max_queue_size: int = 10000):
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._middleware: list[Callable] = []
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._dead_letter: list[Event] = []  # BUG [PERFORMANCE]: Unbounded list grows forever

    def subscribe(self, pattern: str, handler: Handler) -> Callable:
        """Subscribe a handler to a topic pattern. Returns unsubscribe function."""
        self._handlers[pattern].append(handler)

        def unsubscribe():
            # BUG [CORRECTNESS]: Removing by value in list — if same handler subscribed
            # twice to same pattern, this only removes the first occurrence. Also not
            # thread-safe with concurrent subscribe/publish.
            self._handlers[pattern].remove(handler)

        return unsubscribe

    def use(self, middleware: Callable) -> None:
        """Add a middleware function."""
        self._middleware.append(middleware)

    async def publish(self, event: Event) -> None:
        """Publish an event to the bus."""
        if not self._running:
            raise RuntimeError("EventBus is not running. Call start() first.")
        await self._queue.put(event)

    async def start(self) -> None:
        """Start processing events."""
        self._running = True
        asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        """Stop processing events."""
        self._running = False
        # BUG [CORRECTNESS]: Does not drain the queue — events already queued are lost

    async def _process_loop(self) -> None:
        """Main event processing loop."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            # Apply middleware chain
            processed_event = event
            for mw in self._middleware:
                try:
                    processed_event = await mw(processed_event)
                    if processed_event is None:
                        break  # Middleware filtered the event
                except Exception as e:
                    logger.warning(f"Middleware error: {e}")
                    # BUG [ERROR HANDLING]: Continues processing with the ORIGINAL event
                    # after middleware error, not the partially-processed one. Should
                    # either skip the event entirely or stop the middleware chain.
                    break

            if processed_event is None:
                continue

            # Dispatch to matching handlers
            dispatched = False
            for pattern, handlers in self._handlers.items():
                if processed_event.matches(pattern):
                    for handler in handlers:
                        try:
                            await handler(processed_event)
                            dispatched = True
                        except Exception as e:
                            logger.error(f"Handler error for {pattern}: {e}")

            if not dispatched:
                self._dead_letter.append(processed_event)

    # CLEAN — well-implemented status method
    def status(self) -> dict[str, Any]:
        """Get bus status information."""
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "subscriptions": {p: len(h) for p, h in self._handlers.items()},
            "dead_letters": len(self._dead_letter),
        }
