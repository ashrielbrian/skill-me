"""Catalog query normalization, SKU shapes, and page sizing."""

import unittest

from libs.commerce.skuid import normalize_sku
from services.catalog.search import normalize

from tests.conftest import make_settings
from tests.fixtures.products import build_catalog_page, description_html, raw_sku_variants
from tests.helpers.asserts import assert_page_shape, assert_sku_shape, assert_sorted_by


class QueryNormalizeTest(unittest.TestCase):
    def test_lowercases_and_trims(self):
        self.assertEqual(normalize("  Brass LAMP  "), "brass lamp")

    def test_collapses_inner_whitespace(self):
        self.assertEqual(normalize("desk\t  lamp"), "desk lamp")

    def test_blank_query_stays_empty(self):
        self.assertEqual(normalize("   "), "")


class SkuNormalizeTest(unittest.TestCase):
    def test_variants_converge(self):
        # Spacing, casing, and separator differences all land on one SKU.
        results = {normalize_sku(raw) for raw in raw_sku_variants()}
        self.assertEqual(len(results), 1)

    def test_shape_of_normalized_sku(self):
        sku = normalize_sku("lamp 0001 brs")
        assert_sku_shape(self, sku)
        self.assertEqual(sku, "LAMP-0001-BRS")


class PageShapeTest(unittest.TestCase):
    def test_default_page_holds_25_items(self):
        settings = make_settings()
        page = build_catalog_page(size=settings["page_size"])
        assert_page_shape(self, page, size=settings["page_size"])
        self.assertEqual(len(page["items"]), 25)

    def test_last_page_is_partial(self):
        # 120 total items leaves 20 on the fifth page.
        page = build_catalog_page(page=5, size=25, total=120)
        self.assertEqual(len(page["items"]), 20)

    def test_page_past_the_end_is_empty(self):
        page = build_catalog_page(page=9, size=25, total=120)
        self.assertEqual(page["items"], [])

    def test_items_are_ordered_by_sku(self):
        page = build_catalog_page(page=2, size=25, total=120)
        assert_sorted_by(self, page["items"], "sku")


class DescriptionBodyTest(unittest.TestCase):
    def test_body_holds_one_p_tag_per_paragraph(self):
        body = description_html(paragraph_count=4)
        self.assertEqual(body.count("<p>"), 4)
        self.assertEqual(body.count("</p>"), 4)

    def test_body_is_a_single_string(self):
        body = description_html(paragraph_count=2, sentence="Two lamps.")
        self.assertEqual(body, "<p>Two lamps.</p><p>Two lamps.</p>")


if __name__ == "__main__":
    unittest.main()
