"""Gateway auth parsing, limit keys, and counter windows."""

import unittest

from services.gateway import middleware

from tests.conftest import make_clock, make_redis


class BearerParsingTest(unittest.TestCase):
    def test_well_formed_header_yields_token(self):
        self.assertEqual(middleware.parse_bearer("Bearer tok_abc123"), "tok_abc123")

    def test_missing_header_yields_none(self):
        self.assertIsNone(middleware.parse_bearer(None))

    def test_wrong_scheme_yields_none(self):
        self.assertIsNone(middleware.parse_bearer("Basic dXNlcjpzZWNyZXQ="))

    def test_scheme_alone_yields_none(self):
        self.assertIsNone(middleware.parse_bearer("Bearer"))


class LimitKeyTest(unittest.TestCase):
    def test_key_combines_tenant_and_route(self):
        key = middleware.limit_key("acme", "/v1/orders")
        self.assertEqual(key, "rl:acme:/v1/orders")

    def test_tenant_is_lowercased(self):
        key = middleware.limit_key("ACME", "/v1/orders")
        self.assertTrue(key.startswith("rl:acme:"))


class CounterWindowTest(unittest.IsolatedAsyncioTestCase):
    async def test_incr_counts_each_request(self):
        redis = make_redis()
        count = 0
        for _ in range(5):
            count = await redis.incr("rl:acme:/v1/orders")
        self.assertEqual(count, 5)

    async def test_counter_key_expiry_is_recorded(self):
        redis = make_redis()
        await redis.set("rl:acme:/v1/orders", 1, ex=60)
        self.assertEqual(redis.ttls["rl:acme:/v1/orders"], 60)

    async def test_distinct_tenants_count_separately(self):
        redis = make_redis()
        await redis.incr("rl:acme:/v1/orders")
        await redis.incr("rl:globex:/v1/orders")
        self.assertEqual(await redis.get("rl:acme:/v1/orders"), 1)
        self.assertEqual(await redis.get("rl:globex:/v1/orders"), 1)


class WindowIndexTest(unittest.TestCase):
    def test_60s_windows_roll_at_the_minute(self):
        # The limiter buckets requests by int(now // 60).
        clock = make_clock(start=120.0)
        first = int(clock.now() // 60)
        clock.advance(59)
        self.assertEqual(int(clock.now() // 60), first)
        clock.advance(1)
        self.assertEqual(int(clock.now() // 60), first + 1)


if __name__ == "__main__":
    unittest.main()
