"""Tests for the VALUES clause builder."""

import unittest

from libs.dbkit.batch import build_values_clause, flatten_rows


class ValuesClauseTests(unittest.TestCase):
    def test_empty_rows(self):
        self.assertEqual(build_values_clause([]), "")

    def test_single_row_single_column(self):
        self.assertEqual(build_values_clause([(1,)]), "($1)")

    def test_single_row_two_columns(self):
        self.assertEqual(build_values_clause([(1, "a")]), "($1, $2)")

    def test_two_rows_two_columns(self):
        clause = build_values_clause([(1, "a"), (2, "b")])
        self.assertEqual(clause, "($1, $2), ($3, $4)")

    def test_numbering_continues_across_rows(self):
        clause = build_values_clause([(1, 2, 3), (4, 5, 6), (7, 8, 9)])
        self.assertEqual(clause, "($1, $2, $3), ($4, $5, $6), ($7, $8, $9)")

    def test_group_count_matches_row_count(self):
        rows = [(index, index) for index in range(25)]
        clause = build_values_clause(rows)
        self.assertEqual(clause.count("("), 25)
        self.assertIn("$50", clause)


class FlattenRowsTests(unittest.TestCase):
    def test_flattens_in_placeholder_order(self):
        self.assertEqual(flatten_rows([(1, "a"), (2, "b")]), [1, "a", 2, "b"])

    def test_empty(self):
        self.assertEqual(flatten_rows([]), [])


if __name__ == "__main__":
    unittest.main()
