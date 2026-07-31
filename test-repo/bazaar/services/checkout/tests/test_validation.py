"""Tests for orders.validation."""

import unittest

from services.checkout.errors import ValidationFailure
from services.checkout.orders.validation import (
    validate_cart_item_payload,
    validate_order_request,
)


def _payload(**overrides):
    payload = {
        "buyer_id": "buyer_a1b2c3d4",
        "cart_id": "cart_e5f6a7b8",
        "region": "us",
        "items": [
            {"product_id": "prod_c9d0e1f2", "category": "general", "quantity": 2}
        ],
    }
    payload.update(overrides)
    return payload


class ValidateOrderRequestTest(unittest.TestCase):
    def test_accepts_well_formed_payload(self):
        request = validate_order_request(_payload())
        self.assertEqual(request["buyer_id"], "buyer_a1b2c3d4")
        self.assertEqual(request["region"], "us")
        self.assertEqual(len(request["items"]), 1)

    def test_rejects_missing_buyer(self):
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(buyer_id=None))

    def test_rejects_malformed_cart_id(self):
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(cart_id="not-an-id"))

    def test_rejects_unknown_region(self):
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(region="zz"))

    def test_rejects_empty_items(self):
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(items=[]))

    def test_rejects_too_many_items(self):
        items = [{"product_id": "prod_c9d0e1f2", "quantity": 1}] * 101
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(items=items))

    def test_rejects_zero_quantity(self):
        items = [{"product_id": "prod_c9d0e1f2", "quantity": 0}]
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(items=items))

    def test_rejects_missing_product_id(self):
        items = [{"quantity": 1}]
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(items=items))

    def test_passes_promo_code_through(self):
        request = validate_order_request(_payload(promo_code="SUMMER10"))
        self.assertEqual(request["promo_code"], "SUMMER10")

    def test_absent_promo_code_is_none(self):
        request = validate_order_request(_payload())
        self.assertIsNone(request["promo_code"])

    def test_rejects_non_string_promo_code(self):
        with self.assertRaises(ValidationFailure):
            validate_order_request(_payload(promo_code=10))

    def test_collects_multiple_reasons(self):
        try:
            validate_order_request(_payload(buyer_id="x", region="zz"))
        except ValidationFailure as exc:
            self.assertGreaterEqual(len(exc.reasons), 2)
        else:
            self.fail("expected ValidationFailure")


class ValidateCartItemPayloadTest(unittest.TestCase):
    def _line(self, **overrides):
        payload = {
            "product_id": "prod_c9d0e1f2",
            "category": "general",
            "quantity": 2,
            "region": "us",
        }
        payload.update(overrides)
        return payload

    def test_accepts_well_formed_line(self):
        line = validate_cart_item_payload(self._line())
        self.assertEqual(line["quantity"], 2)
        self.assertEqual(line["category"], "general")

    def test_defaults_category_and_region(self):
        payload = {"product_id": "prod_c9d0e1f2", "quantity": 1}
        line = validate_cart_item_payload(payload)
        self.assertEqual(line["category"], "general")
        self.assertEqual(line["region"], "us")

    def test_rejects_malformed_product_id(self):
        with self.assertRaises(ValidationFailure):
            validate_cart_item_payload(self._line(product_id="???"))

    def test_rejects_quantity_above_cap(self):
        with self.assertRaises(ValidationFailure):
            validate_cart_item_payload(self._line(quantity=21))

    def test_rejects_unknown_region(self):
        with self.assertRaises(ValidationFailure):
            validate_cart_item_payload(self._line(region="zz"))


if __name__ == "__main__":
    unittest.main()
