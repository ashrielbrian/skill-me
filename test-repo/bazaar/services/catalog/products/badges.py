"""Listing badge assignment.

Badges derive from fields already on the row; assignment is pure and
runs once per shaped row.
"""

_RATING_THRESHOLD = 4.7
_REVIEW_FLOOR = 25
_NEW_LISTING_DAYS = 14

_LABELS = {
    "top_rated": "Top rated",
    "new_arrival": "New arrival",
    "verified_seller": "Verified seller",
}


def badges_for(row: dict) -> list[str]:
    """Returns display badge codes for one listing row."""
    result = []
    rating = float(row.get("rating", 0.0))
    review_count = int(row.get("review_count", 0))
    if rating >= _RATING_THRESHOLD and review_count >= _REVIEW_FLOOR:
        result.append("top_rated")
    if int(row.get("age_days", 999)) <= _NEW_LISTING_DAYS:
        result.append("new_arrival")
    if row.get("seller_badge") == "verified":
        result.append("verified_seller")
    return result


def badge_label(badge: str) -> str:
    """Maps a badge code to interface copy."""
    return _LABELS.get(badge, badge)
