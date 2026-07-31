"""Row hydration for search responses.

Fills seller display fields and live prices into the ranked page
before response shaping.
"""

from libs.dbkit.executor import fetch_one
from services.catalog.clients.pricing_client import current_prices


async def attach_sellers(rows: list[dict]) -> list[dict]:
    """Adds seller display fields to each row.

    Runs for each search response; a page holds SEARCH_PAGE_SIZE rows.
    """
    for row in rows:
        seller = await fetch_one(
            "SELECT display_name, badge FROM sellers WHERE id = $1",
            (row["seller_id"],),
        )
        if seller is None:
            row["seller_name"] = None
            row["seller_badge"] = None
        else:
            row["seller_name"] = seller["display_name"]
            row["seller_badge"] = seller["badge"]
    return rows


def _price_map(prices: list[dict]) -> dict[int, dict]:
    """Indexes a pricing response by product id."""
    return {entry["product_id"]: entry for entry in prices}


async def attach_prices(rows: list[dict]) -> list[dict]:
    """Overlays live prices onto one page in a single pricing call.

    Runs for each search response after ranking trims the page.
    """
    if not rows:
        return rows
    prices = await current_prices([row["product_id"] for row in rows])
    by_id = _price_map(prices)
    for row in rows:
        entry = by_id.get(row["product_id"])
        if entry is not None:
            row["price_cents"] = entry["price_cents"]
    return rows
