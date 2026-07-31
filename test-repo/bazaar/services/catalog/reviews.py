"""Review aggregates for product detail payloads.

Both queries here are indexed on product_id and run once per uncached
detail request.
"""

from libs.dbkit.executor import fetch_all, fetch_one

_SUMMARY_SQL = (
    "SELECT count(*) AS review_count, "
    "coalesce(avg(rating), 0) AS average_rating "
    "FROM reviews WHERE product_id = $1 AND state = 'published'"
)

_RECENT_SQL = (
    "SELECT author_name, rating, title, created_at "
    "FROM reviews WHERE product_id = $1 AND state = 'published' "
    "ORDER BY created_at DESC LIMIT $2"
)

_HISTOGRAM_SQL = (
    "SELECT rating, count(*) AS n FROM reviews "
    "WHERE product_id = $1 AND state = 'published' GROUP BY rating"
)

RECENT_LIMIT = 3


async def review_summary(product_id: int) -> dict:
    """Returns the aggregate block for one product."""
    row = await fetch_one(_SUMMARY_SQL, (product_id,))
    if row is None:
        return {"count": 0, "average": 0.0}
    return {
        "count": row["review_count"],
        "average": round(float(row["average_rating"]), 2),
    }


async def rating_histogram(product_id: int) -> dict[int, int]:
    """Returns the count per star level; the detail page draws bars."""
    rows = await fetch_all(_HISTOGRAM_SQL, (product_id,))
    histogram = {star: 0 for star in range(1, 6)}
    for row in rows:
        histogram[int(row["rating"])] = row["n"]
    return histogram


async def recent_reviews(product_id: int) -> list[dict]:
    """Returns the newest published reviews for the detail page footer."""
    rows = await fetch_all(_RECENT_SQL, (product_id, RECENT_LIMIT))
    return [
        {
            "author": row["author_name"],
            "rating": row["rating"],
            "title": row["title"],
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]
