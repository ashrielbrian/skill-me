"""Read access to stock rows.

get_stock backs GET /v1/stock/{sku}; get_stock_many serves internal callers
that already hold a SKU list.
"""

from __future__ import annotations

from libs.dbkit.executor import fetch_all, fetch_one

from services.inventory.models import StockLevel

_ONE_SQL = "SELECT sku, available, reserved FROM stock WHERE sku = $1"

_MANY_SQL = """
    SELECT sku, available, reserved
      FROM stock
     WHERE sku = ANY($1)
     ORDER BY sku
"""


def _to_level(row) -> StockLevel:
    return StockLevel(sku=row["sku"], available=row["available"], reserved=row["reserved"])


async def get_stock(sku: str) -> StockLevel | None:
    """One stock row, or None when the SKU is unknown."""
    row = await fetch_one(_ONE_SQL, sku)
    if row is None:
        return None
    return _to_level(row)


async def get_stock_many(skus: list[str]) -> dict[str, StockLevel]:
    """All known stock rows for a SKU list, keyed by SKU, in one query."""
    rows = await fetch_all(_MANY_SQL, skus)
    return {row["sku"]: _to_level(row) for row in rows}
