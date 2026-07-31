"""Removes audit records past the retention window.

Runs from CI weekly, never in the serving path. Retention policy is 400
days for tenant-visible actions; the table carries an index on recorded_at.
"""

from __future__ import annotations

import sys

from tools.common.cli import build_parser
from tools.common.pg import query_one, run

RETENTION_DAYS = 400

_COUNT_SQL = """
    SELECT count(*) AS total
      FROM audit_log
     WHERE recorded_at < now() - make_interval(days => $1)
"""

_DELETE_SQL = """
    DELETE FROM audit_log
     WHERE recorded_at < now() - make_interval(days => $1)
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument("--days", type=int, default=RETENTION_DAYS, help="Retention window in days.")
    args = parser.parse_args(argv)

    if args.days < 30:
        print("refusing a retention window under 30 days", file=sys.stderr)
        return 2
    row = query_one(_COUNT_SQL, args.days)
    total = row["total"] if row is not None else 0
    if args.dry_run:
        print(f"would remove {total} audit records older than {args.days} days")
        return 0
    status = run(_DELETE_SQL, args.days)
    print(f"removed audit records: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
