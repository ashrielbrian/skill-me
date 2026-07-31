"""Product feed batches for the marketing export job.

The export job in tools/ calls feed_batch a few hundred times per
run, a few times per day; it never sits on the shopper request path.
"""

from libs.dbkit.executor import fetch_all
from services.catalog import config, currencies

_FEED_COLUMNS = (
    "id, name, price_cents, currency, category, thumbnail_url, "
    "rating, updated_at"
)

_FEED_SQL = (
    "SELECT " + _FEED_COLUMNS + " FROM products "
    "WHERE status = 'active' AND id > $1 "
    "ORDER BY id LIMIT $2"
)


def _feed_item(row) -> dict:
    """Shapes one row into the feed entry layout."""
    return {
        "id": row["id"],
        "name": row["name"],
        "price_cents": row["price_cents"],
        "currency": row["currency"],
        "category": row["category"],
        "thumbnail_url": row["thumbnail_url"],
        "rating": float(row["rating"]),
        "updated_at": str(row["updated_at"]),
    }


async def feed_batch(after_id: int) -> dict:
    """Returns one keyset page of feed rows plus the next cursor.

    The caller loops; each call issues exactly one query.
    """
    rows = await fetch_all(_FEED_SQL, (after_id, config.FEED_BATCH_SIZE))
    items = [_feed_item(row) for row in rows]
    next_after = rows[-1]["id"] if rows else None
    return {"items": items, "after": next_after, "done": not rows}


def csv_line(item: dict) -> str:
    """Renders one feed entry as a CSV line for the flat-file export."""
    name = item["name"].replace('"', '""')
    return ",".join(
        (
            str(item["id"]),
            '"%s"' % name,
            currencies.format_amount(item["price_cents"], item["currency"]),
            item["category"],
            "%.1f" % item["rating"],
        )
    )
