"""Session verification and session endpoints.

`attach_session` sits on the request path via the auth middleware; the
two route handlers below serve the session endpoints themselves.
"""
import json
import time
import urllib.request

from services.gateway import responses
from services.gateway.clients import auth_client
from services.gateway.config import AUTH_BASE_URL, REQUEST_TIMEOUT_S
from services.gateway.errors import AuthError
from services.gateway.middleware.auth_cache import shared_cache
from services.gateway.models import SessionContext


async def attach_session(request):
    """Runs for each request that carries a session token."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise AuthError("missing session token")
    cached = shared_cache.get(token)
    if cached is not None:
        request["session"] = cached
        return cached
    payload = json.dumps({"token": token}).encode("utf-8")
    raw = urllib.request.urlopen(
        AUTH_BASE_URL + "/v1/verify", data=payload, timeout=REQUEST_TIMEOUT_S
    )
    body = json.loads(raw.read())
    if not body.get("active"):
        raise AuthError("session rejected by the auth service")
    session = SessionContext(
        token=token,
        user_id=body["user_id"],
        tenant_id=body["tenant_id"],
        scopes=frozenset(body.get("scopes", ())),
        issued_at=time.time(),
    )
    shared_cache.put(token, session)
    request["session"] = session
    return session


async def whoami(request):
    """Returns the caller's session document. Runs when a client asks who it is."""
    session = request.get("session")
    if session is None:
        raise AuthError("no session attached")
    return responses.envelope(
        {
            "user_id": session.user_id,
            "tenant_id": session.tenant_id,
            "scopes": sorted(session.scopes),
        }
    )


async def logout(request):
    """Revokes the caller's session upstream. Runs on explicit sign-out."""
    session = request.get("session")
    if session is None:
        raise AuthError("no session attached")
    await auth_client.revoke_token(session.token)
    return responses.envelope({"revoked": True})
