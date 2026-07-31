"""Ledger vs bank statement comparison after submission.

Statement rows arrive on the partner feed during the day; the nightly
run compares them to what the ledger says was paid.
"""

from libs.dbkit.executor import fetch_all

LEDGER_TOTALS_QUERY = (
    "SELECT seller_id, sum(net_cents) AS net_cents "
    "FROM payout_ledger WHERE day = $1 GROUP BY seller_id"
)

STATEMENT_QUERY = (
    "SELECT seller_id, amount_cents "
    "FROM bank_statement_rows WHERE day = $1"
)


async def reconcile_day(day):
    """Compares ledger totals to statement rows. Runs once nightly."""
    ledger_rows = await fetch_all(LEDGER_TOTALS_QUERY, day)
    statement_rows = await fetch_all(STATEMENT_QUERY, day)
    return diff_totals(ledger_rows, statement_rows)


def diff_totals(ledger_rows, statement_rows):
    """Returns per-seller mismatches between ledger and statement."""
    statement_by_seller = {
        row["seller_id"]: row["amount_cents"] for row in statement_rows
    }
    mismatches = []
    for row in ledger_rows:
        reported = statement_by_seller.get(row["seller_id"])
        if reported is None:
            mismatches.append({"seller_id": row["seller_id"], "kind": "missing"})
        elif reported != row["net_cents"]:
            mismatches.append(
                {
                    "seller_id": row["seller_id"],
                    "kind": "amount",
                    "ledger_cents": row["net_cents"],
                    "statement_cents": reported,
                }
            )
    return mismatches
