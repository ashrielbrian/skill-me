"""Error types raised inside the gateway.

Each class carries the HTTP status and machine-readable code that the
middleware chain maps onto the standard failure envelope.
"""


class GatewayError(Exception):
    """Base class for every error the edge maps to a response."""

    status = 500
    code = "internal_error"

    def __init__(self, detail=""):
        super().__init__(detail or self.code)
        self.detail = detail or self.code


class AuthError(GatewayError):
    """The request carried no usable session token."""

    status = 401
    code = "unauthorized"


class ForbiddenError(GatewayError):
    """The session is valid but lacks a required scope."""

    status = 403
    code = "forbidden"


class NotFoundError(GatewayError):
    """The addressed resource does not exist."""

    status = 404
    code = "not_found"


class RateLimitedError(GatewayError):
    """The tenant spent its request budget for the current window."""

    status = 429
    code = "rate_limited"


class UpstreamError(GatewayError):
    """A sibling service call did not complete."""

    status = 502
    code = "upstream_error"
