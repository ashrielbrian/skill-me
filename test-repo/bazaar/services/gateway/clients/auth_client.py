"""HTTP client for the auth service.

One process-wide connection pool; every function makes a single call.
"""
import httpx

from services.gateway.config import AUTH_BASE_URL, REQUEST_TIMEOUT_S
from services.gateway.errors import UpstreamError

_client = httpx.AsyncClient(base_url=AUTH_BASE_URL, timeout=REQUEST_TIMEOUT_S)


async def revoke_token(token):
    """Revokes one session token. Runs on explicit sign-out."""
    resp = await _client.post("/v1/revoke", json={"token": token})
    if resp.status_code >= 500:
        raise UpstreamError("auth revoke call did not complete")
    return resp.status_code == 200


async def introspect(token):
    """Reads token metadata without extending it. Runs from the admin console."""
    resp = await _client.post("/v1/introspect", json={"token": token})
    if resp.status_code != 200:
        return None
    return resp.json()


async def close():
    """Releases the auth client. Runs once at process exit."""
    await _client.aclose()
