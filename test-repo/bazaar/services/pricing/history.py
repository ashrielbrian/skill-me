"""Price history persistence.

Rows are buffered while a batch is applied and written with a single
insert_many call when the batch completes.
"""

import time

from libs.dbkit.batch import insert_many

HISTORY_COLUMNS = ("sku", "region", "currency", "amount_cents", "recorded_at")


class HistoryBuffer:
    """Collects one history row per applied event within a batch."""

    def __init__(self):
        self._rows = []

    def add(self, event):
        """Buffers one row. Called once per applied event."""
        self._rows.append(
            (
                event.sku,
                event.region,
                event.currency,
                event.amount_cents,
                time.time(),
            )
        )

    def pending(self):
        """Returns the number of rows waiting to be written."""
        return len(self._rows)

    async def flush(self):
        """Writes the buffered rows. Called once per consumed batch."""
        if not self._rows:
            return 0
        written = len(self._rows)
        await insert_many("price_history", HISTORY_COLUMNS, self._rows)
        self._rows = []
        return written
