"""Writes the tenant roster to CSV for the finance close.

Runs manually or from CI, never in the serving path. Read-only.
"""

from __future__ import annotations

import argparse
import csv
import sys

from tools.common.pg import query_all

_TENANTS_SQL = """
    SELECT id, name, plan, country, created_at::date AS since, status
      FROM tenants
     ORDER BY id
"""

_COLUMNS = ("id", "name", "plan", "country", "since", "status")


def write_csv(rows: list, stream) -> int:
    writer = csv.writer(stream)
    writer.writerow(_COLUMNS)
    writer.writerows(tuple(row[column] for column in _COLUMNS) for row in rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="-", help="Output path, or - for stdout.")
    args = parser.parse_args(argv)

    rows = query_all(_TENANTS_SQL)
    if args.out == "-":
        count = write_csv(rows, sys.stdout)
    else:
        with open(args.out, "w", newline="") as stream:
            count = write_csv(rows, stream)
    print(f"exported {count} tenants", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
