"""Coverage for warehouse bin placement."""

import re
import unittest

from services.inventory.allocator import bin_weight, choose_bin

_BIN_LABEL_RE = re.compile(r"^A\d{2}-\d$")

_SAMPLE_SKUS = [f"SKU-{index:04d}B" for index in range(50)]


class BinWeightTest(unittest.TestCase):
    def test_deterministic_across_calls(self):
        first = bin_weight("SKU-1200B", "us-east")
        second = bin_weight("SKU-1200B", "us-east")
        self.assertEqual(first, second)

    def test_zone_layout_changes_placement(self):
        east = [bin_weight(sku, "us-east") for sku in _SAMPLE_SKUS]
        west = [bin_weight(sku, "us-west") for sku in _SAMPLE_SKUS]
        self.assertNotEqual(east, west)

    def test_unknown_zones_share_default_layout(self):
        self.assertEqual(
            bin_weight("SKU-1234Z", "sa-east"),
            bin_weight("SKU-1234Z", "af-south"),
        )

    def test_repeat_lookups_come_from_memory(self):
        bin_weight.cache_clear()
        bin_weight("SKU-9000T", "us-east")
        bin_weight("SKU-9000T", "us-east")
        info = bin_weight.cache_info()
        self.assertEqual(info.hits, 1)
        self.assertEqual(info.misses, 1)


class ChooseBinTest(unittest.TestCase):
    def test_label_format(self):
        for sku in _SAMPLE_SKUS[:10]:
            label = choose_bin(sku, "eu-central")
            self.assertRegex(label, _BIN_LABEL_RE)

    def test_stable_for_same_inputs(self):
        self.assertEqual(
            choose_bin("SKU-0042B", "ap-south"),
            choose_bin("SKU-0042B", "ap-south"),
        )


if __name__ == "__main__":
    unittest.main()
