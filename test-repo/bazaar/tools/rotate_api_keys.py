"""Issues replacement API keys for a list of tenants.

Runs manually during credential rotation, never in the serving path. New
keys print to stdout once; the database stores only SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import secrets
import sys

from tools.common.cli import build_parser
from tools.common.output import print_table
from tools.common.pg import run_many

_UPDATE_SQL = """
    UPDATE tenants
       SET api_key_sha256 = $2,
           key_rotated_at = now()
     WHERE id = $1
"""


def issue_keys(tenant_ids: list[str]) -> list[tuple[str, str, str]]:
    """(tenant_id, plaintext key, digest) for each tenant."""
    issued = []
    for tenant_id in tenant_ids:
        key = "bzr_" + secrets.token_urlsafe(24)
        digest = hashlib.sha256(key.encode("ascii")).hexdigest()
        issued.append((tenant_id, key, digest))
    return issued


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument("tenant_ids", nargs="+", help="Tenant ids to rotate.")
    args = parser.parse_args(argv)

    issued = issue_keys(args.tenant_ids)
    if args.dry_run:
        print(f"would rotate keys for {len(issued)} tenants")
        return 0
    run_many(_UPDATE_SQL, [(tenant_id, digest) for tenant_id, _, digest in issued])
    print_table(("tenant_id", "new_key"), [(tenant_id, key) for tenant_id, key, _ in issued])
    return 0


if __name__ == "__main__":
    sys.exit(main())
