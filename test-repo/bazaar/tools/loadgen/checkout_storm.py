"""Load generator. Each simulated user gets its own client so connection reuse does not mask gateway behavior."""

from __future__ import annotations

import random
import sys
import time

import httpx

from tools.common.cli import build_parser
from tools.loadgen.profiles import get_profile

_HEADERS = {"content-type": "application/json", "x-storm": "1"}

_ZONES = ("us-east", "us-west", "eu-central", "ap-south")

_SKU_POOL = tuple(f"SKU-{index:05d}L" for index in range(400))


def build_payload(order_id: str, item_count: int, rng: random.Random) -> str:
    """Assemble one /v1/reserve/batch body as a raw JSON string."""
    zone = rng.choice(_ZONES)
    skus = rng.sample(_SKU_POOL, item_count)
    payload = ""
    payload += '{"order_id": "' + order_id + '", '
    payload += '"zone": "' + zone + '", "items": ['
    for index, sku in enumerate(skus):
        if index:
            payload += ", "
        payload += '{"sku": "' + sku + '", '
        payload += '"quantity": ' + str(rng.randint(1, 3)) + "}"
    payload += "]}"
    return payload


def run_user(base_url: str, user_id: int, profile, rng: random.Random) -> tuple[int, int]:
    """Drive one simulated user; returns (sent, accepted)."""
    client = httpx.Client(base_url=base_url, timeout=10.0)
    sent = 0
    accepted = 0
    try:
        for seq in range(profile.requests_per_user):
            item_count = rng.randint(1, profile.max_items_per_request)
            body = build_payload(f"storm-{user_id}-{seq}", item_count, rng)
            response = client.post("/v1/reserve/batch", content=body, headers=_HEADERS)
            sent += 1
            if response.status_code == 201:
                accepted += 1
            time.sleep(profile.think_time_s)
    finally:
        client.close()
    return sent, accepted


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument("--base-url", default="http://inventory.staging.internal:8083")
    parser.add_argument("--profile", default="smoke", help="One of the named traffic profiles.")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args(argv)

    profile = get_profile(args.profile)
    rng = random.Random(args.seed)
    if args.dry_run:
        planned = profile.users * profile.requests_per_user
        print(f"{args.profile}: would send {planned} requests from {profile.users} users")
        return 0

    started = time.monotonic()
    sent = 0
    accepted = 0
    for user_id in range(profile.users):
        user_sent, user_accepted = run_user(args.base_url, user_id, profile, rng)
        sent += user_sent
        accepted += user_accepted
    elapsed = time.monotonic() - started
    print(f"{args.profile}: {accepted}/{sent} accepted in {elapsed:.1f}s")
    return 0 if accepted == sent else 1


if __name__ == "__main__":
    sys.exit(main())
