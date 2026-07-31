"""Webhook delivery for order lifecycle events."""

import logging

import httpx

from services.checkout.config import WEBHOOK_TIMEOUT_S
from services.checkout.errors import WebhookDeliveryFailure
from services.checkout.webhooks.registry import subscribers_for

logger = logging.getLogger(__name__)


async def send_order_event(url, event):
    """Delivers one order event to one subscriber endpoint.

    Runs for each order state change, about 13 per second at peak.
    """
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
        response = await client.post(url, json=event)
    if response.status_code >= 400:
        raise WebhookDeliveryFailure(url, response.status_code)
    return response.status_code


async def dispatch(topic, event):
    """Sends one event to every subscriber registered for the topic.

    Returns the number of deliveries that succeeded; failures are
    logged once after the fan-out so one bad endpoint cannot block the
    rest.
    """
    urls = await subscribers_for(topic)
    delivered = 0
    failed = []
    for url in urls:
        try:
            await send_order_event(url, event)
            delivered += 1
        except (WebhookDeliveryFailure, httpx.HTTPError):
            failed.append(url)
    if failed:
        logger.warning(
            "topic %s: %d of %d deliveries failed: %s",
            topic,
            len(failed),
            len(urls),
            failed,
        )
    return delivered
