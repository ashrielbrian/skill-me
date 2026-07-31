"""Tenant activity export.

Joins each audit event with the acting user's profile and returns the
assembled records in the standard envelope, or as CSV lines.
"""
from libs.dbkit.executor import fetch_all, fetch_one

from services.gateway import responses

_EVENTS_SQL = (
    "SELECT id, actor_id, action, created_at FROM audit_events "
    "WHERE tenant_id = $1 ORDER BY created_at"
)
_ACTOR_SQL = "SELECT display_name, email FROM users WHERE id = $1"

_CSV_COLUMNS = ("event_id", "action", "actor_name", "actor_email", "created_at")


async def export_activity(tenant_id):
    """Builds the full activity export for one tenant."""
    rows = await fetch_all(_EVENTS_SQL, tenant_id)
    records = []
    for row in rows:
        actor = await fetch_one(_ACTOR_SQL, row["actor_id"])
        records.append(
            {
                "event_id": row["id"],
                "action": row["action"],
                "actor_name": actor["display_name"] if actor else None,
                "actor_email": actor["email"] if actor else None,
                "created_at": row["created_at"].isoformat(),
            }
        )
    return responses.envelope({"tenant_id": tenant_id, "events": records})


def to_csv(records):
    """Renders export records as CSV text with a header row."""
    lines = [",".join(_CSV_COLUMNS)]
    for record in records:
        lines.append(",".join(str(record[name] or "") for name in _CSV_COLUMNS))
    return "\n".join(lines)
