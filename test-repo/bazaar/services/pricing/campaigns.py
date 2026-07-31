"""Campaign SKU roster.

About 2,000 SKUs are on campaign at a time. The roster is loaded when a
worker starts and stays in memory for the life of the process.
"""

from libs.dbkit.executor import fetch_all, fetch_one

ROSTER_QUERY = (
    "SELECT sku FROM campaign_skus "
    "WHERE active AND now() BETWEEN starts_at AND ends_at"
)

WINDOW_QUERY = "SELECT starts_at, ends_at FROM campaigns WHERE code = $1"


async def load_campaign_skus():
    """Returns the SKUs currently on campaign. Called once at startup."""
    rows = await fetch_all(ROSTER_QUERY)
    return [row["sku"] for row in rows]


async def campaign_window(code):
    """Returns (starts_at, ends_at) for one campaign code.

    Used by the admin CLI when scheduling promos.
    """
    row = await fetch_one(WINDOW_QUERY, code)
    if row is None:
        return None
    return row["starts_at"], row["ends_at"]


CODES_QUERY = "SELECT code FROM campaigns WHERE active ORDER BY code"


async def active_campaign_codes():
    """Returns codes for campaigns currently running. Used by the admin CLI."""
    rows = await fetch_all(CODES_QUERY)
    return [row["code"] for row in rows]


def roster_digest(skus):
    """Returns a short summary string for the startup log line."""
    if not skus:
        return "empty roster"
    return f"{len(skus)} skus, first={skus[0]}"
