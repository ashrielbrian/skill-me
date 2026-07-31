"""Related-product strips for the detail page.

One indexed query per uncached detail request; the database applies
the category filter, ordering, and limit.
"""

from libs.dbkit.executor import fetch_all

_RELATED_COLUMNS = "id, name, price_cents, currency, thumbnail_url, rating"

_RELATED_SQL = (
    "SELECT " + _RELATED_COLUMNS + " FROM products "
    "WHERE status = 'active' AND category = $1 AND id <> $2 "
    "ORDER BY rating DESC, id LIMIT $3"
)

RELATED_LIMIT = 8


async def related_products(
    product_id: int, category: str, limit: int = RELATED_LIMIT
) -> list[dict]:
    """Returns the same-category strip for one detail page."""
    rows = await fetch_all(_RELATED_SQL, (category, product_id, limit))
    return [dict(row) for row in rows]


def strip_view(rows: list[dict]) -> list[dict]:
    """Shapes related rows for the response payload."""
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "price_cents": row["price_cents"],
            "currency": row["currency"],
            "thumbnail_url": row["thumbnail_url"],
            "rating": float(row["rating"]),
        }
        for row in rows
    ]
