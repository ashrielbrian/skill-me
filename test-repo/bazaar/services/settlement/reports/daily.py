"""Nightly settlement report assembly."""

from services.settlement.reports.render import format_money_rows, table

FEES_QUERY = (
    "SELECT plan, count(*) AS payouts, sum(fee_cents) AS fee_cents, "
    "sum(net_cents) AS net_cents "
    "FROM payout_ledger WHERE day = $1 GROUP BY plan"
)


async def summarize_fees(conn, day):
    """Aggregates the day's ledger by plan."""
    return await conn.fetch(FEES_QUERY, day)


async def render_report(conn, day):
    """Builds the nightly report text. Runs once per settlement run."""
    header = _header(await summarize_fees(conn, day), day)
    body = _body(await summarize_fees(conn, day))
    footer = _footer(await summarize_fees(conn, day))
    return "\n\n".join((header, body, footer))


def _header(fee_rows, day):
    """Returns the title block with day-level totals."""
    total_fees = sum(row["fee_cents"] for row in fee_rows)
    total_net = sum(row["net_cents"] for row in fee_rows)
    return (
        f"Settlement report for {day.isoformat()}\n"
        f"total fees: {total_fees} cents, total net: {total_net} cents"
    )


def _body(fee_rows):
    """Returns the per-plan table."""
    pairs = format_money_rows(fee_rows)
    return table(("plan", "net"), pairs)


def _footer(fee_rows):
    """Returns the closing payout counts."""
    payouts = sum(row["payouts"] for row in fee_rows)
    return f"{payouts} payouts across {len(fee_rows)} plans"
