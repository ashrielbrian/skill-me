"""Data access for product rows.

Backs the detail endpoint (~500 req/s at peak) and the category
listing pages. Every query goes through libs/dbkit.
"""

from libs.commerce.skuid import normalize_sku
from libs.dbkit.executor import fetch_all, fetch_one


async def get_product(product_id: int):
    """Loads one product row by id.

    Runs once per uncached detail request.
    """
    return await fetch_one(
        "SELECT * FROM products WHERE id = $1",
        (product_id,),
    )


_LISTING_COLUMNS = (
    "id, name, price_cents, currency, seller_id, category, "
    "thumbnail_url, rating"
)


async def get_by_sku(sku: str):
    """Resolves one product by normalized SKU; the admin CLI calls this."""
    return await fetch_one(
        "SELECT " + _LISTING_COLUMNS + " FROM products WHERE sku = $1",
        (normalize_sku(sku),),
    )


async def list_by_category(category: str, limit: int = 24):
    """Lists leading products for one category page, ordered by rating."""
    return await fetch_all(
        "SELECT " + _LISTING_COLUMNS + " FROM products "
        "WHERE status = 'active' AND category = $1 "
        "ORDER BY rating DESC, id LIMIT $2",
        (category, limit),
    )


async def active_count(seller_id: int) -> int:
    """Counts a seller's active listings for the profile card."""
    row = await fetch_one(
        "SELECT count(*) AS n FROM products "
        "WHERE seller_id = $1 AND status = 'active'",
        (seller_id,),
    )
    return int(row["n"]) if row else 0
