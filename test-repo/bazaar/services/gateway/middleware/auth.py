"""Session attachment and gateway error mapping.

Sits third in the middleware chain, after access logging and CORS and
ahead of rate limiting.
"""
from aiohttp import web

from services.gateway import responses
from services.gateway.errors import ForbiddenError, GatewayError
from services.gateway.handlers.session import attach_session

_PUBLIC_PATHS = frozenset({"/healthz", "/readyz"})
_PUBLIC_PREFIXES = ("/v1/products",)
_BEARER_PREFIX = "Bearer "


def _is_public(path):
    """Paths that anonymous clients may reach."""
    if path in _PUBLIC_PATHS:
        return True
    return path.startswith(_PUBLIC_PREFIXES)


def require_scope(request, scope):
    """Confirms the attached session carries one scope. Called by handlers that guard writes."""
    session = request.get("session")
    if session is None or not session.has_scope(scope):
        raise ForbiddenError("missing required scope " + scope)


@web.middleware
async def middleware(request, handler):
    """Attaches the caller identity. Runs for each request that reaches the edge."""
    try:
        header = request.headers.get("authorization", "")
        if not _is_public(request.path) and request.method != "OPTIONS":
            if not header.startswith(_BEARER_PREFIX):
                return responses.failure(
                    "unauthorized", "a session token is required", 401
                )
            await attach_session(request)
        elif header.startswith(_BEARER_PREFIX):
            # Public paths still attach a session when the client sends one.
            await attach_session(request)
        return await handler(request)
    except GatewayError as exc:
        return responses.failure(exc.code, exc.detail, exc.status)
