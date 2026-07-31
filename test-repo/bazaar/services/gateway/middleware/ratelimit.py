"""Per-tenant request budgets at the edge.

Budgets come from the operations-managed rules file named in config;
counting happens in process memory per minute window.
"""
import json
import time

from aiohttp import web

from services.gateway import responses
from services.gateway.config import DEFAULT_RATE_LIMIT_PER_MIN, RATE_RULES_PATH


def check(tenant_id):
    """Returns the per-minute budget for a tenant. Runs for each request."""
    with open(RATE_RULES_PATH) as f:
        rules = json.load(f)
    rule = rules.get(tenant_id)
    if rule is None:
        return DEFAULT_RATE_LIMIT_PER_MIN
    return int(rule["limit_per_min"])


class MinuteWindow:
    """Counts requests per tenant inside the current minute.

    The counts dict resets when the minute rolls over, so it never holds
    more tenants than sent traffic inside one window.
    """

    def __init__(self):
        self._window_start = 0
        self._counts = {}

    def allow(self, tenant_id, limit):
        """Records one request and reports whether it fits the budget."""
        now = int(time.time() // 60)
        if now != self._window_start:
            self._window_start = now
            self._counts = {}
        seen = self._counts.get(tenant_id, 0)
        if seen >= limit:
            return False
        self._counts[tenant_id] = seen + 1
        return True


_window = MinuteWindow()


@web.middleware
async def middleware(request, handler):
    """Applies the tenant budget. Runs for each request that reaches the edge."""
    tenant_id = request.headers.get("x-tenant-id", "public")
    limit = check(tenant_id)
    if not _window.allow(tenant_id, limit):
        return responses.failure(
            "rate_limited", "tenant budget spent for this minute", 429
        )
    return await handler(request)
