"""Money formatting and cents arithmetic across the commerce library."""

import unittest

from libs.commerce.money import format_cents

from tests.fixtures.orders import build_line_item, build_order
from tests.helpers.asserts import assert_cents


class FormatCentsTest(unittest.TestCase):
    def test_zero_renders_two_decimals(self):
        self.assertEqual(format_cents(0), "$0.00")

    def test_under_one_dollar_keeps_leading_zero(self):
        self.assertEqual(format_cents(7), "$0.07")

    def test_exact_dollar(self):
        self.assertEqual(format_cents(100), "$1.00")

    def test_thousands_separator(self):
        self.assertEqual(format_cents(123456), "$1,234.56")

    def test_negative_amount(self):
        self.assertEqual(format_cents(-2500), "-$25.00")


class CentsArithmeticTest(unittest.TestCase):
    def test_line_total_is_qty_times_unit(self):
        item = build_line_item(qty=3, unit_cents=1999)
        assert_cents(self, item["line_cents"], 5997)

    def test_default_order_has_eight_items(self):
        # Eight line items is the documented production average.
        order = build_order()
        self.assertEqual(len(order["items"]), 8)
        assert_cents(self, order["subtotal_cents"], 8 * 1250)

    def test_subtotal_matches_item_sum(self):
        order = build_order(item_count=5, unit_cents=730)
        expected = sum(item["line_cents"] for item in order["items"])
        assert_cents(self, order["subtotal_cents"], expected)
        self.assertEqual(expected, 3650)

    def test_single_item_order(self):
        order = build_order(item_count=1, unit_cents=9_999)
        assert_cents(self, order["subtotal_cents"], 9_999)


class OrderFormattingTest(unittest.TestCase):
    def test_default_order_subtotal_renders(self):
        # Eight items at 1250 cents each is exactly one hundred dollars.
        order = build_order()
        self.assertEqual(format_cents(order["subtotal_cents"]), "$100.00")

    def test_line_total_renders_with_separator(self):
        item = build_line_item(qty=100, unit_cents=1999)
        self.assertEqual(format_cents(item["line_cents"]), "$1,999.00")


if __name__ == "__main__":
    unittest.main()
