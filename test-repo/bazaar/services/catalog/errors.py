"""Error types and the HTTP error middleware for the catalog service.

The middleware wraps every request; typed errors raised by handlers
map to JSON bodies with stable error codes.
"""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


class CatalogError(Exception):
    """Base class for errors the service converts to HTTP responses."""

    status = 500
    code = "internal_error"

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message or self.code


class ValidationError(CatalogError):
    status = 400
    code = "invalid_request"


class NotFoundError(CatalogError):
    status = 404
    code = "not_found"


class UpstreamError(CatalogError):
    status = 502
    code = "upstream_failure"


def to_payload(err: CatalogError) -> dict:
    """Shapes one error into the response body envelope."""
    return {"error": {"code": err.code, "message": err.message}}


@web.middleware
async def error_middleware(request, handler):
    """Converts typed errors to JSON responses. Wraps every request."""
    try:
        return await handler(request)
    except CatalogError as err:
        if err.status >= 500:
            logger.warning("request failed: %s %s", err.code, err.message)
        return web.json_response(to_payload(err), status=err.status)
