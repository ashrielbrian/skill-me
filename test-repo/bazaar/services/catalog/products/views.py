"""Response shaping for product payloads.

The detail view reads exactly the eight fields the interface renders;
shaping runs once per uncached detail request.
"""

DETAIL_FIELDS = (
    "id",
    "name",
    "price_cents",
    "currency",
    "seller_id",
    "category",
    "thumbnail_url",
    "rating",
)


def detail_view(row) -> dict:
    """Builds the detail payload from one product row."""
    return {
        "id": row["id"],
        "name": row["name"],
        "price_cents": row["price_cents"],
        "currency": row["currency"],
        "seller_id": row["seller_id"],
        "category": row["category"],
        "thumbnail_url": row["thumbnail_url"],
        "rating": float(row["rating"]),
    }


def listing_view(rows) -> list[dict]:
    """Shapes a category listing page from repository rows."""
    return [{field: row[field] for field in DETAIL_FIELDS} for row in rows]


def display_price(price_cents: int, currency: str) -> str:
    """Formats a minor-unit price for feed text output."""
    return "%s %.2f" % (currency, price_cents / 100)
