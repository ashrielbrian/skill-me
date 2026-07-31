"""Operator command line for bazaar.

Runs manually, never in the serving path. Subcommands cover the lookups and
corrections operators reach for during a support shift:

    python -m tools.admin stock SKU-A1B2C3
    python -m tools.admin reservations ord-8842
    python -m tools.admin release 41823
"""

from __future__ import annotations

import argparse
import sys

from tools.common.output import print_json, print_table
from tools.common.pg import query_all, query_one, run

_STOCK_SQL = "SELECT sku, available, reserved FROM stock WHERE sku = $1"

_RESERVATIONS_SQL = """
    SELECT id, sku, quantity, status, expires_at
      FROM reservations
     WHERE order_id = $1
     ORDER BY id
"""

_RELEASE_SQL = """
    UPDATE reservations
       SET status = 'released'
     WHERE id = $1
       AND status = 'active'
"""


def cmd_stock(args: argparse.Namespace) -> int:
    row = query_one(_STOCK_SQL, args.sku)
    if row is None:
        print(f"no stock row for {args.sku}", file=sys.stderr)
        return 1
    print_json(dict(row))
    return 0


def cmd_reservations(args: argparse.Namespace) -> int:
    rows = query_all(_RESERVATIONS_SQL, args.order_id)
    if not rows:
        print(f"no reservations for {args.order_id}", file=sys.stderr)
        return 1
    headers = ("id", "sku", "quantity", "status", "expires_at")
    print_table(headers, [tuple(row[name] for name in headers) for row in rows])
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    status = run(_RELEASE_SQL, args.reservation_id)
    print(f"release {args.reservation_id}: {status}")
    return 0


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bazaar-admin", description=__doc__)
    sub = parser.add_subparsers(required=True)

    stock = sub.add_parser("stock", help="Show the stock row for a SKU.")
    stock.add_argument("sku")
    stock.set_defaults(func=cmd_stock)

    reservations = sub.add_parser("reservations", help="List reservations for an order.")
    reservations.add_argument("order_id")
    reservations.set_defaults(func=cmd_reservations)

    release = sub.add_parser("release", help="Release one active reservation.")
    release.add_argument("reservation_id", type=int)
    release.set_defaults(func=cmd_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_cli().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
