"""Tests for the refund amount and the refund status moves."""

import unittest

from services.checkout.orders.models import can_transition
from services.checkout.orders.refund import refund_amount_cents

_ITEMS = [
    {
        "product_id": "prod_a1b2c3",
        "quantity": 2,
        "unit_price_cents": 1500,
        "tax_rate": 0.08,
    },
    {
        "product_id": "prod_d4e5f6",
        "quantity": 1,
        "unit_price_cents": 700,
        "tax_rate": 0.0,
    },
]


class RefundAmountTest(unittest.TestCase):
    def test_sums_every_line_with_tax(self):
        self.assertEqual(refund_amount_cents(_ITEMS), 3240 + 700)

    def test_empty_order_refunds_nothing(self):
        self.assertEqual(refund_amount_cents([]), 0)


class RefundTransitionTest(unittest.TestCase):
    def test_placed_and_settled_orders_can_be_refunded(self):
        self.assertTrue(can_transition("placed", "refunded"))
        self.assertTrue(can_transition("settled", "refunded"))

    def test_other_orders_cannot_be_refunded(self):
        for status in ("pending", "reserved", "cancelled", "refunded"):
            self.assertFalse(can_transition(status, "refunded"))


if __name__ == "__main__":
    unittest.main()
