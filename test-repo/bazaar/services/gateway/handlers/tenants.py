"""Tenant profile and usage endpoints for the admin console."""
from dataclasses import asdict

from libs.commerce.flags import flag
from libs.dbkit.executor import fetch_all, fetch_one

from services.gateway import responses
from services.gateway.middleware.auth import require_scope
from services.gateway.models import TenantRecord

_TENANT_SQL = "SELECT id, name, plan, created_at FROM tenants WHERE id = $1"
_LIST_SQL = (
    "SELECT id, name, plan, created_at FROM tenants "
    "ORDER BY created_at DESC LIMIT $1"
)
_LIST_LIMIT = 200
_USAGE_SQL = (
    "SELECT day, request_count, order_count FROM tenant_usage_daily "
    "WHERE tenant_id = $1 AND day >= CURRENT_DATE - INTERVAL '30 days' "
    "ORDER BY day"
)


async def get_tenant(request):
    """Returns one tenant profile. Runs when the admin console loads a tenant."""
    require_scope(request, "tenant:read")
    tenant_id = request.match_info["tenant_id"]
    row = await fetch_one(_TENANT_SQL, tenant_id)
    if row is None:
        return responses.failure("not_found", "unknown tenant", 404)
    record = TenantRecord(
        id=row["id"],
        name=row["name"],
        plan=row["plan"],
        created_at=row["created_at"].isoformat(),
    )
    return responses.envelope(asdict(record))


async def list_tenants(request):
    """Returns the newest tenants. Runs when the admin console opens its tenant list."""
    require_scope(request, "tenant:read")
    rows = await fetch_all(_LIST_SQL, _LIST_LIMIT)
    tenants = [
        {"id": row["id"], "name": row["name"], "plan": row["plan"]} for row in rows
    ]
    return responses.envelope({"tenants": tenants})


async def get_usage(request):
    """Returns 30 days of usage for one tenant. Runs when the admin console draws usage."""
    require_scope(request, "tenant:read")
    tenant_id = request.match_info["tenant_id"]
    if not flag("tenant_usage_endpoint"):
        return responses.failure("forbidden", "usage endpoint is disabled", 403)
    rows = await fetch_all(_USAGE_SQL, tenant_id)
    days = [
        {
            "day": row["day"].isoformat(),
            "requests": row["request_count"],
            "orders": row["order_count"],
        }
        for row in rows
    ]
    return responses.envelope({"tenant_id": tenant_id, "days": days})
