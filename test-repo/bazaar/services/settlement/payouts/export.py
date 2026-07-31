"""Bank export file for the nightly payouts.

One CSV file per settlement day, written to the export spool directory
and picked up by the transfer job on the banking side.
"""

from pathlib import Path

from services.settlement.config import EXPORT_DIR
from services.settlement.errors import ExportError

HEADER = "seller_id,day,gross_cents,fee_cents,net_cents,currency"


def export_path(day):
    """Returns the spool location for one day's export file."""
    return Path(EXPORT_DIR) / f"payouts-{day.isoformat()}.csv"


def build_export_lines(lines):
    """Returns CSV rows for the day's payout lines. Runs once nightly."""
    out = [HEADER]
    for line in lines:
        out.append(
            ",".join(
                (
                    line.seller_id,
                    line.day.isoformat(),
                    str(line.gross_cents),
                    str(line.fee_cents),
                    str(line.net_cents),
                    line.currency,
                )
            )
        )
    return out


def write_export(rows, day):
    """Writes one export file per day. Runs once nightly."""
    path = export_path(day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ExportError(str(path)) from exc
    return path
