"""Per-seller aggregation over the day's settled orders."""

from services.settlement.config import TOP_SELLERS_LIMIT


def rollup_by_seller(rows):
    """Aggregates gross and net cents per seller. Runs once nightly."""
    totals = {}
    for row in rows:
        entry = totals.get(row["seller_id"])
        if entry is None:
            entry = {
                "gross_cents": 0,
                "net_cents": 0,
                "orders": 0,
                "plan": row["plan"],
            }
            totals[row["seller_id"]] = entry
        entry["gross_cents"] += row["gross_cents"]
        entry["net_cents"] += row["net_cents"]
        entry["orders"] += 1
    return totals


def order_count(totals):
    """Returns the total order count across all sellers."""
    return sum(entry["orders"] for entry in totals.values())


def top_sellers(rows):
    """Returns the rows listed in the nightly summary table."""
    ordered = sorted(rows, key=lambda r: r["net_cents"], reverse=True)
    return ordered[:TOP_SELLERS_LIMIT]
