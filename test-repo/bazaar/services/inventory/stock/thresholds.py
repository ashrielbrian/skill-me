"""Reorder thresholds for stock rows.

The below_floor report backs an operator dashboard that refreshes every few
minutes; classification itself is pure and runs in memory.
"""

from __future__ import annotations

from libs.dbkit.executor import fetch_all

# Default floor applies when a SKU's category has no override.
DEFAULT_FLOOR = 10

_CATEGORY_FLOORS = {
    "appliance": 4,
    "grocery": 40,
    "media": 8,
    "toys": 15,
}

_BELOW_FLOOR_SQL = """
    SELECT s.sku, s.available, p.category
      FROM stock AS s
      JOIN products AS p ON p.sku = s.sku
     WHERE s.available < $1
     ORDER BY s.available
"""


def floor_for(category: str) -> int:
    return _CATEGORY_FLOORS.get(category, DEFAULT_FLOOR)


def classify(available: int, category: str) -> str:
    """Bucket a stock level: "empty", "reorder", or "ok"."""
    if available <= 0:
        return "empty"
    if available < floor_for(category):
        return "reorder"
    return "ok"


async def below_floor_report() -> list[dict]:
    """Rows under the widest floor, classified per category, in one query."""
    widest = max(*_CATEGORY_FLOORS.values(), DEFAULT_FLOOR)
    rows = await fetch_all(_BELOW_FLOOR_SQL, widest)
    return [
        {
            "sku": row["sku"],
            "available": row["available"],
            "state": classify(row["available"], row["category"]),
        }
        for row in rows
    ]
