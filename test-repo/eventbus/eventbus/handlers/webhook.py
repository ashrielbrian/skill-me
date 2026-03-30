"""Webhook delivery handler."""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any
import urllib.request
import urllib.error

from ..event import Event

logger = logging.getLogger(__name__)


class WebhookHandler:
    """Delivers events to webhook URLs via HTTP POST."""

    def __init__(self, url: str, headers: dict[str, str] = None,
                 timeout: int = 30):
        self.url = url
        self.headers = headers or {"Content-Type": "application/json"}
        self.timeout = timeout

    async def __call__(self, event: Event) -> None:
        """Send event to webhook URL."""
        payload = json.dumps({
            "event_id": event.event_id,
            "topic": event.topic,
            "payload": event.payload,
            "timestamp": event.timestamp,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.url,
            data=payload,
            headers=self.headers,
            method="POST",
        )

        # BUG [PERFORMANCE]: Uses synchronous urllib in async handler — blocks the event loop.
        # Should use aiohttp or httpx for async HTTP.
        try:
            response = urllib.request.urlopen(req, timeout=self.timeout)
            if response.status >= 400:
                logger.error(f"Webhook {self.url} returned {response.status}")
        except urllib.error.URLError as e:
            logger.error(f"Webhook delivery failed: {e}")
            raise


# CLEAN — this factory function is fine
def create_webhook_handler(url: str, auth_token: str = None) -> WebhookHandler:
    """Create a webhook handler with optional bearer auth."""
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return WebhookHandler(url=url, headers=headers)
