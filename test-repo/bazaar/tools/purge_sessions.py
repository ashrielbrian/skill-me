"""Removes expired gateway sessions.

Runs from CI on a daily schedule, never in the serving path. Sessions
expire server-side; this script reclaims the rows.
"""

from __future__ import annotations

import sys

from tools.common.cli import build_parser
from tools.common.pg import query_one, run

_COUNT_SQL = "SELECT count(*) AS total FROM sessions WHERE expires_at < now()"

_DELETE_SQL = "DELETE FROM sessions WHERE expires_at < now()"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    args = parser.parse_args(argv)

    row = query_one(_COUNT_SQL)
    total = row["total"] if row is not None else 0
    if args.dry_run:
        print(f"would remove {total} expired sessions")
        return 0
    status = run(_DELETE_SQL)
    print(f"removed expired sessions: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
