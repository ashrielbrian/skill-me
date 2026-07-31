"""Validation coverage for the batch reservation payload parser."""

import unittest

from services.inventory.config import RESERVE_BATCH_MAX
from services.inventory.errors import BatchTooLargeError, ValidationError
from services.inventory.models import parse_batch_payload


def make_payload(count: int) -> dict:
    items = [{"sku": f"SKU-{index:04d}A", "quantity": 1} for index in range(count)]
    return {"order_id": "ord-100", "zone": "us-east", "items": items}


class ParseBatchPayloadTest(unittest.TestCase):
    def test_accepts_single_item(self):
        order_id, zone, items = parse_batch_payload(make_payload(1))
        self.assertEqual(order_id, "ord-100")
        self.assertEqual(zone, "us-east")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].sku, "SKU-0000A")
        self.assertEqual(items[0].quantity, 1)

    def test_accepts_full_batch(self):
        _, _, items = parse_batch_payload(make_payload(RESERVE_BATCH_MAX))
        self.assertEqual(len(items), RESERVE_BATCH_MAX)

    def test_rejects_oversized_batch(self):
        with self.assertRaises(BatchTooLargeError):
            parse_batch_payload(make_payload(RESERVE_BATCH_MAX + 1))

    def test_rejects_empty_items(self):
        payload = make_payload(1)
        payload["items"] = []
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)

    def test_rejects_duplicate_skus(self):
        payload = make_payload(2)
        payload["items"][1]["sku"] = payload["items"][0]["sku"]
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)

    def test_rejects_unknown_zone(self):
        payload = make_payload(1)
        payload["zone"] = "mars-north"
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)

    def test_rejects_zero_quantity(self):
        payload = make_payload(1)
        payload["items"][0]["quantity"] = 0
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)

    def test_rejects_boolean_quantity(self):
        payload = make_payload(1)
        payload["items"][0]["quantity"] = True
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)

    def test_rejects_malformed_sku(self):
        payload = make_payload(1)
        payload["items"][0]["sku"] = "not a sku"
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)

    def test_rejects_missing_order_id(self):
        payload = make_payload(1)
        del payload["order_id"]
        with self.assertRaises(ValidationError):
            parse_batch_payload(payload)


if __name__ == "__main__":
    unittest.main()
