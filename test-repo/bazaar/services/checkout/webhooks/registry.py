"""Webhook subscription storage."""

from urllib.parse import urlsplit

from libs.dbkit.executor import execute, fetch_all, fetch_one
from services.checkout.errors import ValidationFailure

KNOWN_TOPICS = frozenset(
    {
        "order.placed",
        "order.reserved",
        "order.settled",
        "order.cancelled",
    }
)

_SUBSCRIBERS_QUERY = """
    SELECT url FROM webhook_subscriptions WHERE topic = $1 AND active
"""
_INSERT_SUBSCRIPTION = """
    INSERT INTO webhook_subscriptions (topic, url, active, created_at)
    VALUES ($1, $2, TRUE, now())
    RETURNING id
"""
_DEACTIVATE = "UPDATE webhook_subscriptions SET active = FALSE WHERE id = $1"


async def subscribers_for(topic):
    """Returns callback URLs for one topic. Called once per order event."""
    rows = await fetch_all(_SUBSCRIBERS_QUERY, (topic,))
    return [row["url"] for row in rows]


async def add_subscription(topic, url):
    """Registers one callback URL. Called from the admin surface, rarely."""
    if topic not in KNOWN_TOPICS:
        raise ValidationFailure(["unknown topic %s" % topic])
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValidationFailure(["callback URL must be absolute https"])
    row = await fetch_one(_INSERT_SUBSCRIPTION, (topic, url))
    return row["id"]


async def remove_subscription(subscription_id):
    """Deactivates one subscription. Called from the admin surface, rarely."""
    await execute(_DEACTIVATE, (subscription_id,))
