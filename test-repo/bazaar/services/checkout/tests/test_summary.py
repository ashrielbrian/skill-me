"""Tests for orders.summary with the database read stubbed out."""

import json
import unittest
from unittest import mock

from services.checkout.errors import OrderNotFound
from services.checkout.orders import summary

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
        "tax_rate": 0.08,
    },
]
_ROW = {
    "status": "placed",
    "items_json": json.dumps(_ITEMS),
    "updated_at": "2026-07-30T10:00:00Z",
}


class OrderStatusViewTest(unittest.IsolatedAsyncioTestCase):
    async def test_counts_items_and_sums_total(self):
        with mock.patch.object(
            summary, "fetch_one", mock.AsyncMock(return_value=_ROW)
        ):
            view = await summary.order_status_view("ord_123abc")
        self.assertEqual(view["item_count"], 2)
        self.assertEqual(view["total_cents"], 3700)
        self.assertEqual(view["status"], "placed")

    async def test_echoes_order_id(self):
        with mock.patch.object(
            summary, "fetch_one", mock.AsyncMock(return_value=_ROW)
        ):
            view = await summary.order_status_view("ord_123abc")
        self.assertEqual(view["order_id"], "ord_123abc")

    async def test_empty_order_counts_zero(self):
        row = dict(_ROW, items_json="[]")
        with mock.patch.object(
            summary, "fetch_one", mock.AsyncMock(return_value=row)
        ):
            view = await summary.order_status_view("ord_empty")
        self.assertEqual(view["item_count"], 0)
        self.assertEqual(view["total_cents"], 0)

    async def test_queries_by_order_id(self):
        stub = mock.AsyncMock(return_value=_ROW)
        with mock.patch.object(summary, "fetch_one", stub):
            await summary.order_status_view("ord_123abc")
        query_args = stub.await_args.args
        self.assertIn("ord_123abc", query_args[1])

    async def test_missing_order_raises(self):
        with mock.patch.object(
            summary, "fetch_one", mock.AsyncMock(return_value=None)
        ):
            with self.assertRaises(OrderNotFound):
                await summary.order_status_view("ord_missing")


if __name__ == "__main__":
    unittest.main()
