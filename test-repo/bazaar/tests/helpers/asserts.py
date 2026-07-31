"""Assertion helpers shared by the cross-service suites.

Each helper takes the TestCase as its first argument so failures report
through the normal unittest machinery.
"""


def assert_cents(case, value, expected):
    # Money travels as integer cents end to end; floats are never allowed.
    case.assertIsInstance(value, int)
    case.assertEqual(value, expected)


def assert_sku_shape(case, sku):
    """Normalized SKUs are upper-case with single-dash separators."""
    case.assertEqual(sku, sku.upper())
    case.assertNotIn(" ", sku)
    case.assertNotIn("--", sku)
    case.assertFalse(sku.startswith("-"))
    case.assertFalse(sku.endswith("-"))


def assert_page_shape(case, page, size=25):
    """Search pages carry at most `size` items and a running total."""
    case.assertIn("items", page)
    case.assertIn("total", page)
    case.assertLessEqual(len(page["items"]), size)
    case.assertGreaterEqual(page["total"], len(page["items"]))


def assert_sorted_by(case, rows, key):
    values = [row[key] for row in rows]
    case.assertEqual(values, sorted(values))


def assert_event_fields(case, event, required=("event_id", "kind", "occurred_at")):
    for field in required:
        case.assertIn(field, event)


def assert_query_used(case, conn, fragment):
    """Checks that some recorded query on a FakeConn contains `fragment`."""
    joined = "\n".join(query for query, _ in conn.queries)
    case.assertIn(fragment, joined)
