"""Shared fixtures for the library test suite.

Tests exercise the pure helpers directly; nothing here opens a
database or Redis connection. Keep fixtures deterministic so the
suite gives the same answer on every machine, and keep sample
values realistic so failures read like real orders.
"""

SAMPLE_REGION = "US"
SAMPLE_CURRENCY = "USD"


def sample_rows(count=3, width=2):
    """Rows shaped like the batch helpers expect: tuples of scalars."""
    return [
        tuple(range(start, start + width))
        for start in range(0, count * width, width)
    ]


def sample_order_lines():
    """A small fixed cart used by money and order tests."""
    return [
        ("KB-0193-BLK", 1, 4999, "computers"),
        ("MUG-CERAMIC-350ML", 2, 1250, "home_kitchen"),
        ("CBL-USBC-2M", 3, 899, "electronics"),
    ]


def cart_subtotal_cents(lines):
    """Subtotal of the fixture cart in minor units."""
    return sum(quantity * price for _, quantity, price, _ in lines)
