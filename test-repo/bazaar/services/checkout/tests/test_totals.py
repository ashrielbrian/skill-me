"""Tests for carts.totals. Pure math, no I/O."""

import unittest

from services.checkout.carts.totals import (
    cart_breakdown,
    grand_total_cents,
    line_breakdown,
    shipping_cents,
    subtotal_cents,
    tax_cents,
)
from services.checkout.orders.models import LineItem


def _item(price, quantity, tax_rate=0.0):
    return LineItem(
        product_id="prod_a1b2c3",
        category="general",
        quantity=quantity,
        unit_price_cents=price,
        tax_rate=tax_rate,
    )


class SubtotalTest(unittest.TestCase):
    def test_empty_cart_is_zero(self):
        self.assertEqual(subtotal_cents([]), 0)

    def test_sums_price_times_quantity(self):
        items = [_item(1000, 2), _item(250, 4)]
        self.assertEqual(subtotal_cents(items), 3000)


class TaxTest(unittest.TestCase):
    def test_zero_rate_yields_zero(self):
        self.assertEqual(tax_cents([_item(1000, 1)]), 0)

    def test_rounds_each_line_separately(self):
        # 333 * 0.1 = 33.3, rounds to 33 on its own line.
        items = [_item(333, 1, tax_rate=0.1), _item(333, 1, tax_rate=0.1)]
        self.assertEqual(tax_cents(items), 66)


class ShippingTest(unittest.TestCase):
    def test_known_region_flat_rate(self):
        self.assertEqual(shipping_cents(1000, "us"), 599)

    def test_unknown_region_uses_default(self):
        self.assertEqual(shipping_cents(1000, "br"), 1299)

    def test_threshold_ships_free(self):
        self.assertEqual(shipping_cents(7500, "us"), 0)

    def test_just_below_threshold_still_charges(self):
        self.assertEqual(shipping_cents(7499, "us"), 599)


class LineBreakdownTest(unittest.TestCase):
    def test_reports_line_fields(self):
        line = line_breakdown(_item(1200, 3, tax_rate=0.05))
        self.assertEqual(line["line_subtotal_cents"], 3600)
        self.assertEqual(line["line_tax_cents"], 180)
        self.assertEqual(line["quantity"], 3)


class CartBreakdownTest(unittest.TestCase):
    def test_summary_matches_its_parts(self):
        items = [_item(1000, 2, tax_rate=0.1), _item(500, 1)]
        summary = cart_breakdown(items, "us")
        self.assertEqual(summary["subtotal_cents"], 2500)
        self.assertEqual(summary["tax_cents"], 200)
        self.assertEqual(summary["shipping_cents"], 599)
        self.assertEqual(summary["total_cents"], 3299)
        self.assertEqual(len(summary["lines"]), 2)

    def test_total_equals_grand_total(self):
        items = [_item(2000, 4, tax_rate=0.08)]
        summary = cart_breakdown(items, "eu")
        self.assertEqual(summary["total_cents"], grand_total_cents(items, "eu"))


class GrandTotalTest(unittest.TestCase):
    def test_combines_subtotal_tax_and_shipping(self):
        items = [_item(1000, 2, tax_rate=0.1)]
        # 2000 subtotal + 200 tax + 599 shipping.
        self.assertEqual(grand_total_cents(items, "us"), 2799)

    def test_free_shipping_above_threshold(self):
        items = [_item(8000, 1)]
        self.assertEqual(grand_total_cents(items, "us"), 8000)


if __name__ == "__main__":
    unittest.main()
