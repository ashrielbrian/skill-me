"""Response envelope helpers for the gateway edge.

Every handler and middleware returns through these helpers so bodies and
security headers stay uniform across endpoints.
"""
import json

from aiohttp import web

_HEADER_PAIRS = [
    ("x-frame-options", "DENY"),
    ("x-content-type-options", "nosniff"),
    ("strict-transport-security", "max-age=63072000; includeSubDomains"),
    ("referrer-policy", "no-referrer"),
    ("cross-origin-opener-policy", "same-origin"),
    ("cache-control", "no-store"),
]


def envelope(payload, status=200):
    """Wraps a payload in the standard body. Runs for each response the edge returns."""
    headers = {}
    for name, value in _HEADER_PAIRS:
        headers[name] = value
    headers["content-type"] = "application/json"
    body = json.dumps({"status": status, "data": payload})
    return web.Response(status=status, headers=headers, body=body.encode("utf-8"))


def failure(code, detail, status):
    """Builds the standard error body. Runs for each rejected request."""
    return envelope({"code": code, "detail": detail}, status=status)


def no_content():
    """Builds an empty 204 response. Used by delete-style endpoints."""
    return web.Response(status=204)
