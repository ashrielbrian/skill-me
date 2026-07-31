"""Tests for money conversion and formatting."""

import unittest

from libs.commerce.errors import CurrencyError
from libs.commerce.money import convert, format_cents


class ConvertTests(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(convert(1000, "USD", "USD"), 1000)

    def test_eur_to_usd(self):
        self.assertEqual(convert(1000, "EUR", "USD"), 1090)

    def test_round_trip_is_close(self):
        cents = convert(convert(50_000, "USD", "EUR"), "EUR", "USD")
        self.assertLessEqual(abs(cents - 50_000), 1)

    def test_zero_amount(self):
        self.assertEqual(convert(0, "GBP", "JPY"), 0)

    def test_unknown_source_code(self):
        with self.assertRaises(CurrencyError):
            convert(100, "ZZZ", "USD")

    def test_unknown_target_code(self):
        with self.assertRaises(CurrencyError):
            convert(100, "USD", "ZZZ")


class FormatTests(unittest.TestCase):
    def test_two_decimal_currency(self):
        self.assertEqual(format_cents(12345, "USD"), "123.45 USD")

    def test_zero_decimal_currency(self):
        self.assertEqual(format_cents(500, "JPY"), "500 JPY")

    def test_negative_amount(self):
        self.assertEqual(format_cents(-250, "EUR"), "-2.50 EUR")

    def test_small_amount_keeps_two_places(self):
        self.assertEqual(format_cents(5, "USD"), "0.05 USD")

    def test_three_decimal_currency(self):
        self.assertEqual(format_cents(12345, "KWD"), "12.345 KWD")


if __name__ == "__main__":
    unittest.main()
