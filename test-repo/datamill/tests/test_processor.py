"""Tests for processor module."""
import pytest
import pandas as pd
from datamill.processor import process_file, aggregate_file, validate_schema


class TestProcessFile:
    def test_load_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("name,age\nAlice,30\nBob,25")
        result = process_file(str(csv_file))
        assert len(result) == 2

    def test_filter(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("name,age\nAlice,30\nBob,25\nCharlie,35")
        result = process_file(str(csv_file), filter_expr="age > 28")
        assert len(result) == 2


class TestAggregateFile:
    def test_sum(self, tmp_path):
        csv_file = tmp_path / "sales.csv"
        csv_file.write_text("region,revenue\nEast,100\nWest,200\nEast,150")
        result = aggregate_file(str(csv_file), "region", sum_col="revenue")
        assert "revenue_sum" in result.columns


class TestValidateSchema:
    def test_valid_schema(self):
        df = pd.DataFrame({"name": ["Alice"], "age": [30]})
        errors = validate_schema(df, {"name": "object", "age": "int64"})
        assert len(errors) == 0

    def test_missing_column(self):
        df = pd.DataFrame({"name": ["Alice"]})
        errors = validate_schema(df, {"name": "object", "age": "int64"})
        assert any("Missing" in e for e in errors)
