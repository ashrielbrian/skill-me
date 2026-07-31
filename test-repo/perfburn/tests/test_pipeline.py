"""Pipeline tests."""

import unittest

from perfburn.ingest import parser
from perfburn.ingest.dedupe import Deduper


def make_events(count=10000):
    blob = ""
    for i in range(count):
        blob += (
            f"id=evt{i} level=info source_id=s{i % 5}"
            f" ip=10.{i % 200}.0.{i % 255} ts=2026-07-30T00:00:00\n")
    return blob


class ParserTest(unittest.TestCase):
    def test_parse_line(self):
        fields = parser.parse_line('id=evt1 level=error msg="disk full"')
        self.assertEqual(fields["id"], "evt1")
        self.assertEqual(fields["level"], "error")
        self.assertEqual(fields["msg"], "disk full")

    def test_normalize_is_sorted(self):
        canonical = parser.normalize({"b": "2", "a": "1"})
        self.assertEqual(canonical, "a=1;b=2;")

    def test_bulk_parse(self):
        lines = make_events().splitlines()
        parsed = [parser.parse_line(line) for line in lines]
        self.assertEqual(len(parsed), 10000)


class DedupeTest(unittest.TestCase):
    def test_seen_twice(self):
        deduper = Deduper()
        self.assertFalse(deduper.seen("evt1"))
        self.assertTrue(deduper.seen("evt1"))

    def test_capacity_evicts_oldest(self):
        deduper = Deduper(capacity=2)
        deduper.seen("a")
        deduper.seen("b")
        deduper.seen("c")
        self.assertFalse(deduper.seen("a"))


if __name__ == "__main__":
    unittest.main()
