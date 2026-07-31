"""Payout ledger writes.

Lines are written in fixed-size batches; each batch is followed by one
audit record so the run can be traced end to end.
"""

import logging

from libs.commerce.audit import audit_context, write_audit
from libs.dbkit.batch import insert_many

from services.settlement.config import LEDGER_BATCH_SIZE

LOG = logging.getLogger(__name__)

LEDGER_COLUMNS = (
    "seller_id",
    "day",
    "gross_cents",
    "fee_cents",
    "net_cents",
    "currency",
)


def _chunks(items, size):
    """Yields consecutive slices of the given size."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def write_ledger(lines):
    """Writes payout lines in LEDGER_BATCH_SIZE batches. Runs once nightly."""
    written = 0
    for batch in _chunks(lines, LEDGER_BATCH_SIZE):
        rows = [
            (
                line.seller_id,
                line.day,
                line.gross_cents,
                line.fee_cents,
                line.net_cents,
                line.currency,
            )
            for line in batch
        ]
        await insert_many("payout_ledger", LEDGER_COLUMNS, rows)
        await write_audit(audit_context())
        written += len(rows)
    LOG.info("wrote %d ledger rows", written)
    return written
