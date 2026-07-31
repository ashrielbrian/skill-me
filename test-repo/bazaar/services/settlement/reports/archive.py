"""Retention for rendered nightly reports.

One text file per day is kept in the export spool directory; files
older than RETENTION_DAYS are removed after each run.
"""

import logging
from pathlib import Path

from services.settlement.config import EXPORT_DIR, RETENTION_DAYS

LOG = logging.getLogger(__name__)


def report_path(day):
    """Returns the on-disk location for one day's report."""
    return Path(EXPORT_DIR) / "reports" / f"report-{day.isoformat()}.txt"


def store_report(report_text, day):
    """Writes one report file per day. Runs once nightly."""
    path = report_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")
    return path


def prune_reports():
    """Deletes report files beyond RETENTION_DAYS. Runs once nightly."""
    directory = Path(EXPORT_DIR) / "reports"
    if not directory.exists():
        return 0
    names = sorted(directory.glob("report-*.txt"), reverse=True)
    removed = 0
    for stale in names[RETENTION_DAYS:]:
        stale.unlink()
        removed += 1
    if removed:
        LOG.info("pruned %d report files", removed)
    return removed
