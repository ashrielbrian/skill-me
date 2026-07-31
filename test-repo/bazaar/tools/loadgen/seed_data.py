"""Seeds synthetic products and stock rows into a staging database.

Runs manually before a load test, never in the serving path.
"""

from __future__ import annotations

import random
import sys

from tools.common.cli import build_parser
from tools.common.pg import run_many

_CATEGORIES = ("appliance", "grocery", "media", "toys", "outdoors")

_PRODUCT_SQL = """
    INSERT INTO products (sku, tenant_id, title, category, price_cents)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (sku) DO NOTHING
"""

_STOCK_SQL = """
    INSERT INTO stock (sku, available, reserved)
    VALUES ($1, $2, 0)
    ON CONFLICT (sku) DO NOTHING
"""


def build_rows(count: int, seed: int) -> tuple[list[tuple], list[tuple]]:
    """Deterministic synthetic rows so repeat runs line up."""
    rng = random.Random(seed)
    products = []
    stock = []
    for index in range(count):
        sku = f"SKU-{index:05d}L"
        category = rng.choice(_CATEGORIES)
        tenant_id = f"tenant-{rng.randint(1, 40):03d}"
        title = f"{category} item {index}"
        price_cents = rng.randint(199, 99999)
        products.append((sku, tenant_id, title, category, price_cents))
        stock.append((sku, rng.randint(20, 900)))
    return products, stock


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(__doc__)
    parser.add_argument("--count", type=int, default=5000, help="Products to create.")
    parser.add_argument("--seed", type=int, default=7, help="Seed for the row generator.")
    args = parser.parse_args(argv)

    products, stock = build_rows(args.count, args.seed)
    if args.dry_run:
        print(f"would insert {len(products)} products and stock rows")
        return 0
    run_many(_PRODUCT_SQL, products)
    run_many(_STOCK_SQL, stock)
    print(f"seeded {len(products)} products")
    return 0


if __name__ == "__main__":
    sys.exit(main())
