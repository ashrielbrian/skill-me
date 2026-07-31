"""Cross-origin request handling for browser clients.

Only the origins listed in config receive allow headers; requests from
other origins pass through without them and the browser enforces policy.
"""
from aiohttp import web

from services.gateway.config import ALLOWED_ORIGINS

_ALLOW_METHODS = "GET, POST, PUT, DELETE, OPTIONS"
_ALLOW_HEADERS = "authorization, content-type, x-tenant-id"
_MAX_AGE_S = "600"


def _apply(response, origin):
    """Stamps the origin headers onto one response."""
    response.headers["access-control-allow-origin"] = origin
    response.headers["access-control-allow-methods"] = _ALLOW_METHODS
    response.headers["access-control-allow-headers"] = _ALLOW_HEADERS
    response.headers["access-control-max-age"] = _MAX_AGE_S
    response.headers["access-control-expose-headers"] = "x-request-id"
    response.headers["vary"] = "origin"
    return response


@web.middleware
async def middleware(request, handler):
    """Answers preflights and stamps origin headers. Runs for each request that reaches the edge."""
    origin = request.headers.get("origin", "")
    if origin and origin not in ALLOWED_ORIGINS:
        origin = ""
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    if origin:
        _apply(response, origin)
    return response
