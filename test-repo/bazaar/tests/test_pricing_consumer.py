"""Pricing event decode, dedupe, and stream read limits."""

import unittest

from libs.eventkit import codec
from services.pricing import consumer

from tests.conftest import make_redis, make_redis_with_stream
from tests.fixtures.events import encode_frames, event_batch, price_event, promo_event
from tests.helpers.asserts import assert_event_fields


class CodecRoundTripTest(unittest.TestCase):
    def test_price_event_survives_encode_decode(self):
        event = price_event(sku="WID-0042-BLK", price_cents=4242, seq=42)
        self.assertEqual(codec.decode(codec.encode(event)), event)

    def test_decoded_event_carries_required_fields(self):
        decoded = codec.decode(codec.encode(price_event()))
        assert_event_fields(self, decoded)


class PromoEventTest(unittest.TestCase):
    def test_promo_round_trip(self):
        event = promo_event(code="SUMMER10", percent_off=10, seq=9)
        self.assertEqual(codec.decode(codec.encode(event)), event)

    def test_promo_carries_required_fields(self):
        assert_event_fields(self, promo_event())

    def test_percent_off_stays_within_bounds(self):
        event = promo_event(percent_off=10)
        self.assertGreaterEqual(event["percent_off"], 0)
        self.assertLessEqual(event["percent_off"], 100)


class FrameEncodingTest(unittest.TestCase):
    def test_batch_encodes_one_line_per_event(self):
        frames = encode_frames(event_batch(count=100))
        self.assertEqual(len(frames.splitlines()), 100)

    def test_frames_use_sorted_json_keys(self):
        frames = encode_frames([price_event(seq=7)])
        self.assertTrue(frames.startswith('{"event_id"'))


class StreamReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_xrange_honors_the_100_entry_limit(self):
        redis = make_redis()
        for event in event_batch(count=120):
            await redis.xadd("prices", event)
        entries = await redis.xrange("prices", count=100)
        self.assertEqual(len(entries), 100)

    async def test_preloaded_stream_keeps_entry_order(self):
        redis = make_redis_with_stream("prices", event_batch(count=5))
        entries = await redis.xrange("prices")
        # Entry ids are assigned in append order, so seq order holds.
        ids = [int(entry_id.split("-")[0]) for entry_id, _ in entries]
        self.assertEqual(ids, [1, 2, 3, 4, 5])


class DedupeTest(unittest.TestCase):
    def test_repeated_event_ids_collapse_to_one(self):
        batch = event_batch(count=3) + event_batch(count=3)
        self.assertEqual(len(consumer.dedupe(batch)), 3)

    def test_first_occurrence_order_is_kept(self):
        batch = event_batch(count=4)
        deduped = consumer.dedupe(list(batch) + [batch[0]])
        self.assertEqual(
            [event["event_id"] for event in deduped],
            [event["event_id"] for event in batch],
        )


if __name__ == "__main__":
    unittest.main()
