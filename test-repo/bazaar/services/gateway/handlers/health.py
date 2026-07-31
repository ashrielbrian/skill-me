"""Liveness and readiness endpoints.

The load balancer polls both paths every few seconds; neither requires
a session token.
"""
import os
import time

from libs.dbkit.pool import get_pool

from services.gateway import responses
from services.gateway.config import SERVICE_NAME

_STARTED_AT = time.monotonic()
_BUILD_VERSION = os.environ.get("GATEWAY_BUILD_VERSION", "dev")


async def liveness(request):
    """Answers the process-up probe. Runs every few seconds from the load balancer."""
    return responses.envelope(
        {
            "service": SERVICE_NAME,
            "status": "ok",
            "version": _BUILD_VERSION,
            "uptime_s": int(time.monotonic() - _STARTED_AT),
        }
    )


async def readiness(request):
    """Confirms the database pool answers. Runs every few seconds from the load balancer."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return responses.envelope({"service": SERVICE_NAME, "status": "ready"})
