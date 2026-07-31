"""Rebuilds the catalog search projection after a mapping change.

Runs manually or from CI, never in the serving path. The rebuild happens
entirely inside Postgres: one TRUNCATE and one INSERT ... SELECT.
"""

from __future__ import annotations

import sys

from tools.common.cli import build_parser, confirm
from tools.common.pg import query_one, run

_COUNT_SQL = "SELECT count(*) AS total FROM products WHERE discontinued_at IS NULL"

_TRUNCATE_SQL = "TRUNCATE product_search_index"

_REBUILD_SQL = """
    INSERT INTO product_search_index (sku, tenant_id, title_tokens, category, updated_at)
    SELECT sku,
           tenant_id,
           to_tsvector('english', title),
           category,
           now()
      FROM products
     WHERE discontinued_at IS NULL
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args(argv)

    row = query_one(_COUNT_SQL)
    total = row["total"] if row is not None else 0
    if args.dry_run:
        print(f"would reindex {total} products")
        return 0
    if not confirm(f"Rebuild the search index over {total} products?", assume_yes=args.yes):
        print("aborted")
        return 1
    run(_TRUNCATE_SQL)
    status = run(_REBUILD_SQL)
    print(f"rebuilt index: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
