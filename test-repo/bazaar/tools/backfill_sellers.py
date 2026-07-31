"""One-shot backfill for the 2.8 seller-id migration. Run manually."""

from __future__ import annotations

import sys

from tools.common.cli import build_parser
from tools.common.pg import query_all, run_many

_LEGACY_SQL = """
    SELECT legacy_id, email, display_name, created_at::date AS signup_date
      FROM legacy_sellers
     WHERE migrated_at IS NULL
"""

_CURRENT_SQL = """
    SELECT id, email, display_name, created_at::date AS signup_date
      FROM sellers
     WHERE legacy_id IS NULL
"""

_LINK_SQL = "UPDATE sellers SET legacy_id = $2 WHERE id = $1"

_MARK_SQL = "UPDATE legacy_sellers SET migrated_at = now() WHERE legacy_id = $1"


def match_legacy_rows(legacy: list, current: list) -> list[tuple]:
    """Pair each legacy seller with its current row.

    Exact email match wins; otherwise display name plus signup date covers
    accounts whose email changed during the migration window. A current row
    links to at most one legacy row.
    """
    matches = []
    matched_ids: set = set()
    for old in legacy:
        chosen = None
        for row in current:
            if row["id"] in matched_ids:
                continue
            if row["email"] == old["email"]:
                chosen = row
                break
            if (chosen is None
                    and row["display_name"] == old["display_name"]
                    and row["signup_date"] == old["signup_date"]):
                chosen = row
        if chosen is not None:
            matched_ids.add(chosen["id"])
            matches.append((chosen["id"], old["legacy_id"]))
    return matches


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    args = parser.parse_args(argv)

    legacy = query_all(_LEGACY_SQL)
    current = query_all(_CURRENT_SQL)
    matches = match_legacy_rows(legacy, current)
    unmatched = len(legacy) - len(matches)
    if args.dry_run:
        print(f"would link {len(matches)} sellers; {unmatched} left unmatched")
        return 0
    run_many(_LINK_SQL, matches)
    run_many(_MARK_SQL, [(legacy_id,) for _, legacy_id in matches])
    print(f"linked {len(matches)} sellers; {unmatched} left unmatched")
    return 0 if unmatched == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
