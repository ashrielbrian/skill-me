"""Seller profile cards for the seller endpoint.

Profile pages draw a small fraction of catalog traffic; each request
issues two indexed queries.
"""

from libs.dbkit.executor import fetch_all, fetch_one
from services.catalog.errors import NotFoundError

_CARD_COLUMNS = "id, display_name, badge, joined_at, rating, listing_count"

_BADGE_LABELS = {
    "verified": "Verified seller",
    "top": "Top seller",
    "new": "New seller",
}


def badge_label(badge: str | None) -> str | None:
    """Maps a stored badge code to interface copy."""
    if badge is None:
        return None
    return _BADGE_LABELS.get(badge, badge)


async def seller_card(seller_id: int) -> dict:
    """Loads one seller profile. Runs once per seller-page request."""
    row = await fetch_one(
        "SELECT " + _CARD_COLUMNS + " FROM sellers WHERE id = $1",
        (seller_id,),
    )
    if row is None:
        raise NotFoundError("seller %d" % seller_id)
    recent = await fetch_all(
        "SELECT id, name, thumbnail_url FROM products "
        "WHERE seller_id = $1 AND status = 'active' "
        "ORDER BY created_at DESC LIMIT 6",
        (seller_id,),
    )
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "badge": badge_label(row["badge"]),
        "joined_at": str(row["joined_at"]),
        "rating": float(row["rating"]),
        "listing_count": row["listing_count"],
        "recent_products": [dict(item) for item in recent],
    }
