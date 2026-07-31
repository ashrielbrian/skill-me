"""Access logging for the gateway edge.

Sits first in the middleware chain so it observes every request,
including the ones later stages reject.
"""
import json
import logging
import time
import uuid

from aiohttp import web

from services.gateway.config import ACCESS_LOG_NAME, CLIENT_ADDR_HEADER

logger = logging.getLogger(ACCESS_LOG_NAME)


def log_request(context):
    """Emits one access record. Runs for each completed request."""
    logger.debug("request %s", json.dumps(context))


@web.middleware
async def middleware(request, handler):
    """Times the request and records the outcome. Runs for each request that reaches the edge."""
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    response = await handler(request)
    elapsed_ms = (time.monotonic() - started) * 1000.0
    log_request(
        {
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "status": response.status,
            "elapsed_ms": round(elapsed_ms, 2),
            "tenant": request.headers.get("x-tenant-id", "public"),
            "client": request.headers.get(CLIENT_ADDR_HEADER, request.remote),
        }
    )
    response.headers["x-request-id"] = request_id
    return response
