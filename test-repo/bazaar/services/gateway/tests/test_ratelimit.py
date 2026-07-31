"""Checks for the tenant budget logic."""
import json
import unittest
from unittest import mock

from services.gateway.middleware import ratelimit


class CheckTests(unittest.TestCase):
    def test_known_tenant_uses_configured_limit(self):
        rules = json.dumps({"acme": {"limit_per_min": 120}})
        with mock.patch("builtins.open", mock.mock_open(read_data=rules)):
            self.assertEqual(ratelimit.check("acme"), 120)

    def test_unknown_tenant_uses_default(self):
        rules = json.dumps({"acme": {"limit_per_min": 120}})
        with mock.patch("builtins.open", mock.mock_open(read_data=rules)):
            self.assertEqual(
                ratelimit.check("nobody"), ratelimit.DEFAULT_RATE_LIMIT_PER_MIN
            )


class MinuteWindowTests(unittest.TestCase):
    def test_denies_once_budget_is_spent(self):
        window = ratelimit.MinuteWindow()
        with mock.patch("time.time", return_value=1_700_000_000.0):
            self.assertTrue(window.allow("acme", 2))
            self.assertTrue(window.allow("acme", 2))
            self.assertFalse(window.allow("acme", 2))

    def test_new_minute_resets_counts(self):
        window = ratelimit.MinuteWindow()
        with mock.patch("time.time", return_value=1_700_000_000.0):
            self.assertTrue(window.allow("acme", 1))
            self.assertFalse(window.allow("acme", 1))
        with mock.patch("time.time", return_value=1_700_000_060.0):
            self.assertTrue(window.allow("acme", 1))

    def test_zero_budget_denies_immediately(self):
        window = ratelimit.MinuteWindow()
        with mock.patch("time.time", return_value=1_700_000_000.0):
            self.assertFalse(window.allow("acme", 0))

    def test_tenants_count_independently(self):
        window = ratelimit.MinuteWindow()
        with mock.patch("time.time", return_value=1_700_000_000.0):
            self.assertTrue(window.allow("acme", 1))
            self.assertTrue(window.allow("globex", 1))
            self.assertFalse(window.allow("acme", 1))


if __name__ == "__main__":
    unittest.main()
