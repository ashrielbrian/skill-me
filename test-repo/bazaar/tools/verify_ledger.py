"""Cross-checks payout ledger entries against settled order totals.

Runs manually or from CI after the settlement batch; read-only. A non-zero
exit code means at least one seller's ledger sum drifted from the order sum.
"""

from __future__ import annotations

import argparse
import sys

from tools.common.output import print_table
from tools.common.pg import query_all

_LEDGER_SQL = """
    SELECT seller_id, sum(amount_cents) AS total_cents
      FROM payout_ledger
     WHERE entry_date = $1
     GROUP BY seller_id
"""

_ORDERS_SQL = """
    SELECT seller_id, sum(net_cents) AS total_cents
      FROM settled_orders
     WHERE settlement_date = $1
     GROUP BY seller_id
"""


def find_mismatches(ledger_rows: list, order_rows: list) -> list[tuple]:
    """Sellers whose ledger and order totals disagree, with both sums."""
    ledger = {row["seller_id"]: row["total_cents"] for row in ledger_rows}
    orders = {row["seller_id"]: row["total_cents"] for row in order_rows}
    mismatches = []
    for seller_id in sorted(ledger.keys() | orders.keys()):
        ledger_total = ledger.get(seller_id, 0)
        order_total = orders.get(seller_id, 0)
        if ledger_total != order_total:
            mismatches.append(
                (seller_id, ledger_total, order_total, ledger_total - order_total)
            )
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Settlement date to check, YYYY-MM-DD.")
    args = parser.parse_args(argv)

    ledger_rows = query_all(_LEDGER_SQL, args.date)
    order_rows = query_all(_ORDERS_SQL, args.date)
    mismatches = find_mismatches(ledger_rows, order_rows)
    if not mismatches:
        print(f"ledger matches orders for {args.date}")
        return 0
    print_table(("seller_id", "ledger_cents", "order_cents", "drift"), mismatches)
    return 1


if __name__ == "__main__":
    sys.exit(main())
