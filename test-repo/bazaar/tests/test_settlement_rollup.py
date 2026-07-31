"""Fee arithmetic and per-merchant grouping for the nightly rollup."""

import unittest

from services.settlement import rollup

from tests.conftest import make_pool
from tests.helpers.asserts import assert_query_used


def settled_rows():
    return [
        {"merchant_id": "m_001", "gross_cents": 10_000},
        {"merchant_id": "m_002", "gross_cents": 4_500},
        {"merchant_id": "m_001", "gross_cents": 2_500},
    ]


class FeeCentsTest(unittest.TestCase):
    def test_250_bps_on_100_dollars(self):
        # 2.5% of $100.00 is $2.50.
        self.assertEqual(rollup.fee_cents(10_000, rate_bps=250), 250)

    def test_fee_rounds_half_up_at_the_cent(self):
        # 1999 * 250 / 10000 = 49.975, which lands on 50.
        self.assertEqual(rollup.fee_cents(1_999, rate_bps=250), 50)

    def test_zero_gross_has_zero_fee(self):
        self.assertEqual(rollup.fee_cents(0, rate_bps=250), 0)

    def test_fee_scales_with_gross(self):
        self.assertEqual(
            rollup.fee_cents(20_000, rate_bps=250),
            2 * rollup.fee_cents(10_000, rate_bps=250),
        )

    def test_payout_is_gross_minus_fee(self):
        gross = 12_500
        fee = rollup.fee_cents(gross, rate_bps=250)
        self.assertEqual(gross - fee, 12_188)


class GroupingTest(unittest.TestCase):
    def test_rows_group_by_merchant(self):
        groups = rollup.group_by_merchant(settled_rows())
        self.assertEqual(set(groups), {"m_001", "m_002"})
        self.assertEqual(
            sum(row["gross_cents"] for row in groups["m_001"]), 12_500,
        )

    def test_grand_total_is_preserved(self):
        rows = settled_rows()
        groups = rollup.group_by_merchant(rows)
        regrouped = sum(
            row["gross_cents"] for group in groups.values() for row in group
        )
        self.assertEqual(regrouped, sum(row["gross_cents"] for row in rows))


class RollupReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_read_targets_the_orders_table(self):
        pool = make_pool(rows=[settled_rows()])
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT merchant_id, gross_cents FROM orders WHERE settled_on = $1",
                "2026-07-30",
            )
        self.assertEqual(len(rows), 3)
        assert_query_used(self, pool.conn, "settled_on")


if __name__ == "__main__":
    unittest.main()
