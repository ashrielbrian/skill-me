"""Nightly settlement entry point.

Runs once per day at SETTLEMENT_HOUR_UTC over the previous day's
orders. The process is started under systemd and stays resident
between runs.
"""

import asyncio
import logging

from libs.dbkit.executor import fetch_all
from libs.dbkit.pool import get_pool

from services.settlement.banking import submit_transfers
from services.settlement.payouts.adjustments import merge_adjustments
from services.settlement.payouts.export import build_export_lines, write_export
from services.settlement.payouts.fees import build_payout_lines
from services.settlement.payouts.ledger import write_ledger
from services.settlement.payouts.rollup import rollup_by_seller, top_sellers
from services.settlement.reconcile import reconcile_day
from services.settlement.reports.archive import prune_reports, store_report
from services.settlement.reports.daily import render_report
from services.settlement.reports.deliver import deliver_report
from services.settlement.reports.render import table
from services.settlement.windows import previous_utc_day, seconds_until_next_run

LOG = logging.getLogger(__name__)

ORDERS_QUERY = (
    "SELECT seller_id, order_id, gross_cents, net_cents, currency, plan "
    "FROM settled_orders WHERE settlement_day = $1"
)

ADJUSTMENTS_QUERY = (
    "SELECT seller_id, amount_cents, reason "
    "FROM payout_adjustments WHERE day = $1"
)


async def run_once():
    """One full settlement pass over the previous day."""
    day = previous_utc_day()
    rows = await fetch_all(ORDERS_QUERY, day)
    LOG.info("settling %d order rows for %s", len(rows), day)

    totals = rollup_by_seller(rows)
    adjustments = await fetch_all(ADJUSTMENTS_QUERY, day)
    merged = merge_adjustments(totals, adjustments)
    lines = build_payout_lines(totals, day)
    await write_ledger(lines)

    await asyncio.to_thread(write_export, build_export_lines(lines), day)
    await submit_transfers(lines)

    pool = await get_pool()
    async with pool.acquire() as conn:
        report = await render_report(conn, day)
    leaders = top_sellers(rows)
    leader_rows = [(row["seller_id"], str(row["net_cents"])) for row in leaders]
    report = report + "\n\n" + table(("seller", "net_cents"), leader_rows)

    await deliver_report(report, day)
    await asyncio.to_thread(store_report, report, day)
    await asyncio.to_thread(prune_reports)
    mismatches = await reconcile_day(day)
    LOG.info(
        "settlement finished for %s: %d payout lines, %d adjustments, %d mismatches",
        day,
        len(lines),
        merged,
        len(mismatches),
    )


async def main():
    """Sleeps until the run hour, then executes one pass per day."""
    logging.basicConfig(level=logging.INFO)
    while True:
        await asyncio.sleep(seconds_until_next_run())
        await run_once()


if __name__ == "__main__":
    asyncio.run(main())
