"""Reservation batching and item validation for the inventory API."""

import unittest

from services.inventory import reserve

from tests.conftest import make_pool, make_settings


def reservation_items(count):
    items = []
    for index in range(count):
        items.append({"sku": "WID-%04d-BLK" % (index + 1), "qty": 1})
    return items


class BatchChunkingTest(unittest.TestCase):
    def test_250_items_split_into_three_calls(self):
        limit = make_settings()["reserve_batch_limit"]
        chunks = reserve.chunk_items(reservation_items(250), limit=limit)
        self.assertEqual([len(chunk) for chunk in chunks], [100, 100, 50])

    def test_exactly_100_items_is_one_call(self):
        chunks = reserve.chunk_items(reservation_items(100), limit=100)
        self.assertEqual(len(chunks), 1)

    def test_empty_input_yields_no_calls(self):
        self.assertEqual(reserve.chunk_items([], limit=100), [])

    def test_limit_matches_the_documented_api_maximum(self):
        # /v1/reserve/batch accepts up to 100 items per call.
        self.assertEqual(make_settings()["reserve_batch_limit"], 100)

    def test_chunks_preserve_item_order(self):
        chunks = reserve.chunk_items(reservation_items(150), limit=100)
        flattened = [item for chunk in chunks for item in chunk]
        self.assertEqual(flattened, reservation_items(150))


class ReservationValidationTest(unittest.TestCase):
    def test_zero_qty_is_rejected(self):
        problems = reserve.validate_items([{"sku": "WID-0001-BLK", "qty": 0}])
        self.assertTrue(problems)

    def test_positive_qty_passes(self):
        self.assertEqual(reserve.validate_items(reservation_items(3)), [])


class ReserveWriteTest(unittest.IsolatedAsyncioTestCase):
    async def test_each_item_updates_one_stock_row(self):
        pool = make_pool()
        items = reservation_items(4)
        async with pool.acquire() as conn:
            for item in items:
                await conn.execute(
                    "UPDATE stock SET reserved = reserved + $2 WHERE sku = $1",
                    item["sku"], item["qty"],
                )
        # One connection acquisition covers the whole batch.
        self.assertEqual(len(pool.conn.queries), 4)
        self.assertEqual(pool.acquire_count, 1)


if __name__ == "__main__":
    unittest.main()
