"""Checks for the response envelope helpers."""
import json
import unittest

from services.gateway import responses


class EnvelopeTests(unittest.TestCase):
    def test_body_wraps_payload_with_status(self):
        resp = responses.envelope({"ok": True})
        body = json.loads(resp.body)
        self.assertEqual(body["status"], 200)
        self.assertEqual(body["data"], {"ok": True})

    def test_status_is_carried_on_the_response(self):
        resp = responses.envelope({}, status=201)
        self.assertEqual(resp.status, 201)

    def test_security_headers_present(self):
        resp = responses.envelope({})
        self.assertEqual(resp.headers["x-frame-options"], "DENY")
        self.assertEqual(resp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(resp.headers["cache-control"], "no-store")

    def test_content_type_is_json(self):
        resp = responses.envelope({})
        self.assertEqual(resp.headers["content-type"], "application/json")

    def test_every_configured_header_is_stamped(self):
        resp = responses.envelope({})
        for name, value in responses._HEADER_PAIRS:
            self.assertEqual(resp.headers[name], value)


class FailureTests(unittest.TestCase):
    def test_failure_carries_code_and_detail(self):
        resp = responses.failure("not_found", "unknown product", 404)
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 404)
        self.assertEqual(body["data"]["code"], "not_found")
        self.assertEqual(body["data"]["detail"], "unknown product")

    def test_no_content_is_empty(self):
        resp = responses.no_content()
        self.assertEqual(resp.status, 204)


if __name__ == "__main__":
    unittest.main()
