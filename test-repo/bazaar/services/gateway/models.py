"""Data shapes shared across the gateway."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Verified caller identity attached to a request.

    Built once per session verification and reused from the token cache
    for later requests carrying the same token.
    """

    token: str
    user_id: str
    tenant_id: str
    scopes: frozenset
    issued_at: float

    def has_scope(self, scope):
        """True when the session grants one named scope."""
        return scope in self.scopes


@dataclass(frozen=True, slots=True)
class TenantRecord:
    """One row from the tenants table, shaped for the admin console."""

    id: str
    name: str
    plan: str
    created_at: str
