"""Tests for SKU normalization."""

import unittest

from libs.commerce.errors import ValidationError
from libs.commerce.skuid import is_canonical, normalize_sku


class NormalizeSkuTests(unittest.TestCase):
    def test_uppercases(self):
        self.assertEqual(normalize_sku("kb-0193-blk"), "KB-0193-BLK")

    def test_spaces_become_dashes(self):
        self.assertEqual(normalize_sku("kb 0193 blk"), "KB-0193-BLK")

    def test_underscores_and_dots(self):
        self.assertEqual(normalize_sku("kb_0193.blk"), "KB-0193-BLK")

    def test_unicode_dashes_fold(self):
        self.assertEqual(normalize_sku("KB–0193—BLK"), "KB-0193-BLK")

    def test_dash_runs_collapse(self):
        self.assertEqual(normalize_sku("KB--0193---BLK"), "KB-0193-BLK")

    def test_surrounding_space_trimmed(self):
        self.assertEqual(normalize_sku("  KB-0193  "), "KB-0193")

    def test_blank_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_sku("   ")

    def test_none_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_sku(None)

    def test_illegal_characters_rejected(self):
        with self.assertRaises(ValidationError):
            normalize_sku("KB!0193")


class IsCanonicalTests(unittest.TestCase):
    def test_accepts_canonical(self):
        self.assertTrue(is_canonical("KB-0193-BLK"))

    def test_rejects_lower_case(self):
        self.assertFalse(is_canonical("kb-0193-blk"))


if __name__ == "__main__":
    unittest.main()
