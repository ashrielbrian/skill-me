"""Order validation and the placement round trip through the pool."""

import unittest

from services.checkout.orders import validation

from tests.conftest import make_pool
from tests.fixtures.events import encode_frames, order_event
from tests.fixtures.orders import build_order, order_rows, render_order_summary
from tests.helpers.asserts import assert_event_fields, assert_query_used


class ValidateOrderTest(unittest.TestCase):
    def test_valid_order_has_no_problems(self):
        order = build_order()
        self.assertEqual(validation.validate_order(order), [])

    def test_zero_qty_line_is_rejected(self):
        order = build_order(item_count=1)
        order["items"][0]["qty"] = 0
        self.assertTrue(validation.validate_order(order))

    def test_negative_unit_price_is_rejected(self):
        order = build_order(item_count=1)
        order["items"][0]["unit_cents"] = -100
        self.assertTrue(validation.validate_order(order))

    def test_empty_order_is_rejected(self):
        order = build_order(item_count=0)
        self.assertTrue(validation.validate_order(order))


class OrderSummaryTest(unittest.TestCase):
    def test_summary_lists_every_item_once(self):
        order = build_order(item_count=8)
        summary = render_order_summary(order)
        lines = summary.splitlines()
        # Header, eight item lines, and the subtotal line.
        self.assertEqual(len(lines), 10)
        self.assertIn("subtotal %d" % order["subtotal_cents"], lines[-1])

    def test_summary_names_the_currency(self):
        order = build_order(currency="EUR")
        self.assertIn("(EUR)", render_order_summary(order).splitlines()[0])


class WebhookEventTest(unittest.TestCase):
    def test_status_event_carries_envelope_fields(self):
        event = order_event(order_id="ord_0007", status="placed", seq=7)
        assert_event_fields(self, event)
        self.assertEqual(event["order_id"], "ord_0007")

    def test_frames_batch_one_line_per_event(self):
        events = [order_event(seq=index + 1) for index in range(3)]
        self.assertEqual(len(encode_frames(events).splitlines()), 3)


class PlacementRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_status_poll_reads_expected_row(self):
        pool = make_pool(rows=[order_rows(count=1)])
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM orders WHERE order_id = $1", "ord_0001",
            )
        self.assertEqual(row["status"], "placed")
        assert_query_used(self, pool.conn, "FROM orders")

    async def test_insert_records_one_query_per_item(self):
        order = build_order(item_count=3)
        pool = make_pool()
        async with pool.acquire() as conn:
            for item in order["items"]:
                await conn.execute(
                    "INSERT INTO order_items (order_id, sku, qty, unit_cents)"
                    " VALUES ($1, $2, $3, $4)",
                    order["order_id"], item["sku"], item["qty"], item["unit_cents"],
                )
        self.assertEqual(len(pool.conn.queries), 3)
        self.assertEqual(pool.acquire_count, 1)

    async def test_open_order_count_uses_a_scalar_read(self):
        pool = make_pool(scalars=[42])
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM orders WHERE status = $1", "pending",
            )
        self.assertEqual(count, 42)


if __name__ == "__main__":
    unittest.main()
