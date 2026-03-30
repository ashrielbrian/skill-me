"""Data processing and transformation logic."""
import pandas as pd
import os
import tempfile
import subprocess
from typing import Optional


def process_file(path: str, filter_expr: Optional[str] = None,
                 output_format: str = "csv") -> pd.DataFrame:
    """Load, optionally filter, and return a DataFrame."""
    df = _load_file(path)

    if filter_expr:
        # BUG [SECURITY]: eval() on user-supplied filter expression — arbitrary code execution
        df = df[eval(filter_expr, {"__builtins__": {}}, df.to_dict("list"))]

    return df


def _load_file(path: str) -> pd.DataFrame:
    """Load a file based on extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path)
    elif ext == ".json":
        return pd.read_json(path)
    elif ext == ".parquet":
        return pd.read_parquet(path)
    # BUG [ERROR HANDLING]: No else clause — returns None implicitly for unknown extensions
    # This causes AttributeError downstream when caller tries df.to_csv()


def aggregate_file(path: str, group_by: str, sum_col: str = None,
                   mean_col: str = None) -> pd.DataFrame:
    """Aggregate a dataset by grouping."""
    df = _load_file(path)
    grouped = df.groupby(group_by)

    results = {}
    if sum_col:
        results[f"{sum_col}_sum"] = grouped[sum_col].sum()
    if mean_col:
        results[f"{mean_col}_mean"] = grouped[mean_col].mean()

    # BUG [CORRECTNESS]: If neither sum_col nor mean_col provided, results is empty dict
    # pd.DataFrame(results) returns empty DataFrame with no useful info, no error raised
    return pd.DataFrame(results)


def convert_format(input_path: str, output_path: str, target_format: str):
    """Convert between file formats using pandas."""
    df = _load_file(input_path)

    if target_format == "xlsx":
        df.to_excel(output_path, index=False)
    elif target_format == "csv":
        df.to_csv(output_path, index=False)
    elif target_format == "json":
        df.to_json(output_path)
    else:
        # BUG [SECURITY]: Shell injection via user-controlled format string
        subprocess.run(f"csvtool --format {target_format} {input_path} > {output_path}",
                       shell=True, check=True)


def validate_schema(df: pd.DataFrame, schema: dict) -> list:
    """Validate DataFrame against an expected schema."""
    errors = []
    for col, expected_type in schema.items():
        if col not in df.columns:
            errors.append(f"Missing column: {col}")
        # This is CORRECT — the dtype check is fine
        elif not pd.api.types.is_dtype_equal(df[col].dtype, expected_type):
            errors.append(f"Column {col}: expected {expected_type}, got {df[col].dtype}")
    return errors


# CLEAN CODE — this function is well-written and should NOT be flagged
def deduplicate(df: pd.DataFrame, subset: list[str] = None,
                keep: str = "first") -> pd.DataFrame:
    """Remove duplicate rows, optionally by subset of columns."""
    original_len = len(df)
    result = df.drop_duplicates(subset=subset, keep=keep)
    removed = original_len - len(result)
    if removed > 0:
        print(f"Removed {removed} duplicate rows")
    return result
