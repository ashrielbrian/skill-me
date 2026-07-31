"""Checks for the session token cache."""
import unittest

from services.gateway.middleware.auth_cache import SessionCache


class SessionCacheTests(unittest.TestCase):
    def test_returns_stored_value(self):
        cache = SessionCache(ttl_s=60, max_entries=4)
        cache.put("tok-1", "session-1")
        self.assertEqual(cache.get("tok-1"), "session-1")

    def test_missing_token_reads_as_none(self):
        cache = SessionCache(ttl_s=60, max_entries=4)
        self.assertIsNone(cache.get("tok-9"))

    def test_expired_entry_reads_as_missing(self):
        cache = SessionCache(ttl_s=0, max_entries=4)
        cache.put("tok-1", "session-1")
        self.assertIsNone(cache.get("tok-1"))

    def test_insert_beyond_cap_drops_oldest(self):
        cache = SessionCache(ttl_s=60, max_entries=2)
        cache.put("tok-1", "a")
        cache.put("tok-2", "b")
        cache.put("tok-3", "c")
        self.assertIsNone(cache.get("tok-1"))
        self.assertEqual(cache.get("tok-2"), "b")
        self.assertEqual(cache.get("tok-3"), "c")

    def test_reinsert_after_expiry_serves_the_new_value(self):
        cache = SessionCache(ttl_s=0, max_entries=4)
        cache.put("tok-1", "old")
        self.assertIsNone(cache.get("tok-1"))
        fresh = SessionCache(ttl_s=60, max_entries=4)
        fresh.put("tok-1", "new")
        self.assertEqual(fresh.get("tok-1"), "new")

    def test_replacing_a_token_keeps_size_stable(self):
        cache = SessionCache(ttl_s=60, max_entries=2)
        cache.put("tok-1", "a")
        cache.put("tok-1", "a2")
        cache.put("tok-2", "b")
        self.assertEqual(cache.get("tok-1"), "a2")
        self.assertEqual(cache.get("tok-2"), "b")
        self.assertEqual(len(cache), 2)


if __name__ == "__main__":
    unittest.main()
