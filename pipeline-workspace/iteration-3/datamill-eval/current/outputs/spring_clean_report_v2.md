# Codebase Audit Report

## Summary
Datamill is a Python CLI tool for processing, filtering, aggregating, and enriching CSV/JSON datasets, built with Click, Pandas, and Requests. The codebase has several critical security vulnerabilities -- including arbitrary code execution via `eval()` on user input, shell injection in format conversion, and a hardcoded production API key in source code -- that make it unsafe for any environment handling untrusted input. Overall health is poor: while the core structure is reasonable and some utilities are well-written, the security and error handling issues are severe enough to warrant immediate remediation before any production use.

## Findings

### [Critical] Arbitrary code execution via `eval()` on user-supplied filter expression
- **Category**: Security
- **Location**: `datamill/processor.py`, line 16
- **Problem**: The `process_file` function passes the user-supplied `filter_expr` string directly to Python's `eval()`. Although `__builtins__` is set to an empty dict, this is not a sufficient sandbox -- it can be bypassed to achieve arbitrary code execution (e.g., via `__import__` accessed through object introspection). Since the filter expression comes directly from the CLI `--filter` argument, any user of the tool can execute arbitrary Python code.
- **Evidence**:
  ```python
  df = df[eval(filter_expr, {"__builtins__": {}}, df.to_dict("list"))]
  ```
- **Suggested fix**: Replace `eval()` with Pandas' built-in `DataFrame.query()` method, which supports a safe subset of expressions for filtering. Alternatively, implement a simple expression parser that only allows comparison operators on column names.
- **Effort**: Medium (hours)

### [Critical] Shell injection via user-controlled format string in `convert_format`
- **Category**: Security
- **Location**: `datamill/processor.py`, lines 63-64
- **Problem**: When `target_format` is not one of the recognized formats ("xlsx", "csv", "json"), the code falls through to a `subprocess.run()` call with `shell=True` that interpolates both the `target_format` and `input_path` parameters directly into the shell command string. An attacker can inject arbitrary shell commands through either parameter (e.g., `target_format="csv; rm -rf /"` or a malicious file path).
- **Evidence**:
  ```python
  subprocess.run(f"csvtool --format {target_format} {input_path} > {output_path}",
                 shell=True, check=True)
  ```
- **Suggested fix**: Use `subprocess.run()` with a list of arguments instead of `shell=True`, and validate `target_format` against an allowlist of supported formats. Sanitize all path arguments. Better yet, raise an error for unsupported formats rather than shelling out to an external tool.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded production API key in source code
- **Category**: Security
- **Location**: `datamill/config.py`, line 9 and lines 37-42
- **Problem**: A production API key (`dk_prod_a8f3b2c1d4e5f6789012345678901234`) is hardcoded as a constant `FALLBACK_API_KEY` and used as a fallback in `get_api_key()` when no key is provided via config or environment variables. This key is committed to version control and visible to anyone with access to the repository. If this is a real key, it could be used by unauthorized parties or revoked accidentally.
- **Evidence**:
  ```python
  FALLBACK_API_KEY = "dk_prod_a8f3b2c1d4e5f6789012345678901234"
  ```
  ```python
  def get_api_key(config: dict) -> str:
      key = config.get("api_key") or os.environ.get("ENRICHMENT_API_KEY")
      if not key:
          return FALLBACK_API_KEY
      return key
  ```
- **Suggested fix**: Remove the hardcoded key entirely. If no API key is provided, raise a clear error telling the user to set the `ENRICHMENT_API_KEY` environment variable. Rotate the exposed key immediately if it is real.
- **Effort**: Small (< 1 hour)

### [High] API key logged in plaintext at INFO level
- **Category**: Security
- **Location**: `datamill/enricher.py`, line 23
- **Problem**: The `enrich_file` function logs the full API key at `INFO` level. In any deployment where logs are collected (which is most), this leaks the credential to log aggregation systems, log files on disk, and anyone with log access. INFO-level logs are typically not suppressed, making this very likely to be captured.
- **Evidence**:
  ```python
  logger.info(f"Starting enrichment with API key: {api_key}")
  ```
- **Suggested fix**: Remove the API key from the log message entirely, or at most log a masked version (e.g., the last 4 characters).
- **Effort**: Small (< 1 hour)

### [High] Unsafe YAML loading allows arbitrary code execution
- **Category**: Security
- **Location**: `datamill/config.py`, line 22
- **Problem**: The config loader uses `yaml.FullLoader` which, while safer than `yaml.Loader`, still allows instantiation of arbitrary Python objects in some PyYAML versions. A malicious YAML configuration file could exploit this to execute arbitrary code. The PyYAML documentation recommends `yaml.SafeLoader` unless full YAML tag support is specifically needed.
- **Evidence**:
  ```python
  config = yaml.load(f, Loader=yaml.FullLoader)
  ```
- **Suggested fix**: Replace `yaml.FullLoader` with `yaml.SafeLoader`, or use `yaml.safe_load(f)`. The configuration schema only requires basic types (strings, integers, lists), so SafeLoader is sufficient.
- **Effort**: Small (< 1 hour)

### [High] Default API endpoint uses HTTP instead of HTTPS
- **Category**: Security
- **Location**: `datamill/enricher.py`, line 12; `datamill/config.py`, line 33
- **Problem**: The default enrichment API URL uses `http://` rather than `https://`. All API requests -- including the `Authorization: Bearer` header containing the API key -- are sent in plaintext, making them vulnerable to interception and man-in-the-middle attacks on any network. This URL appears in two places: the `DEFAULT_API_URL` constant in `enricher.py` and the `_default_config()` in `config.py`.
- **Evidence**:
  ```python
  DEFAULT_API_URL = "http://api.enrichment-service.com/v2/lookup"
  ```
  ```python
  "api_url": "http://api.enrichment-service.com/v2/lookup",
  ```
- **Suggested fix**: Change both URLs to use `https://`. Consider adding validation that rejects `http://` URLs unless an explicit `--insecure` flag is passed.
- **Effort**: Small (< 1 hour)

### [High] Unbounded recursive retry on HTTP 429 can cause stack overflow
- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, lines 49-53
- **Problem**: When the enrichment API returns a 429 (rate limited) response, `_lookup_batch` recursively calls itself with no depth limit. If the API continues to return 429 responses (e.g., because the rate limit window is long, or the key is permanently throttled), this will eventually hit Python's recursion limit and crash with a `RecursionError`. Each recursive call also holds a stack frame with the full query list, wasting memory.
- **Evidence**:
  ```python
  elif response.status_code == 429:
      retry_after = int(response.headers.get("Retry-After", 5))
      logger.warning(f"Rate limited. Retrying after {retry_after}s")
      time.sleep(retry_after)
      return _lookup_batch(queries, api_key, config)
  ```
- **Suggested fix**: Convert the recursion to a loop with a maximum retry count (e.g., 3-5 attempts). After exhausting retries, raise an exception or return a clear error rather than silently recursing.
- **Effort**: Small (< 1 hour)

### [High] `_load_file` returns `None` for unsupported file extensions
- **Category**: Error Handling
- **Location**: `datamill/processor.py`, lines 21-30
- **Problem**: The `_load_file` function handles `.csv`, `.json`, and `.parquet` extensions but has no `else` clause. For any other extension, the function implicitly returns `None`. Every caller (including `process_file`, `aggregate_file`, and `convert_format`) then tries to call methods on `None`, producing a confusing `AttributeError` instead of a clear error message.
- **Evidence**:
  ```python
  def _load_file(path: str) -> pd.DataFrame:
      ext = os.path.splitext(path)[1].lower()
      if ext == ".csv":
          return pd.read_csv(path)
      elif ext == ".json":
          return pd.read_json(path)
      elif ext == ".parquet":
          return pd.read_parquet(path)
      # No else clause — returns None implicitly
  ```
- **Suggested fix**: Add an `else` clause that raises a `ValueError` with a message listing the supported formats.
- **Effort**: Small (< 1 hour)

### [Medium] No timeout on HTTP requests to enrichment API
- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, line 44
- **Problem**: The `requests.post()` call in `_lookup_batch` has no `timeout` parameter. If the enrichment API becomes unresponsive, the request will hang indefinitely, blocking the entire CLI process with no way for the user to recover other than killing it.
- **Evidence**:
  ```python
  response = requests.post(url, json={"queries": queries}, headers=headers)
  ```
- **Suggested fix**: Add a reasonable `timeout` parameter (e.g., `timeout=30` for 30 seconds). Make it configurable via the config dict.
- **Effort**: Small (< 1 hour)

### [Medium] API errors silently return empty results
- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, lines 55-57
- **Problem**: When the enrichment API returns any non-200/non-429 status code (e.g., 500 Internal Server Error, 401 Unauthorized, 403 Forbidden), the function logs an error but returns an empty list. The caller in `enrich_file` merges this empty result via a left join, so the output DataFrame will have `NaN` values in all enrichment columns with no indication that enrichment failed. The user sees "Enriched N rows" as if everything succeeded.
- **Evidence**:
  ```python
  else:
      logger.error(f"API error: {response.status_code}")
      return []
  ```
- **Suggested fix**: Raise an exception for 4xx/5xx errors (at minimum for auth errors like 401/403), or add a warning to the CLI output indicating how many rows failed enrichment. Consider making this behavior configurable (fail-fast vs. best-effort).
- **Effort**: Medium (hours)

### [Medium] Fixed sleep between batches ignores rate limit headers
- **Category**: Performance
- **Location**: `datamill/enricher.py`, line 32
- **Problem**: The enrichment loop applies a fixed `time.sleep(1)` after every batch, regardless of the API's actual rate limit. For large datasets, this adds unnecessary latency (e.g., 100 batches = 100 seconds of sleeping). The API already returns `Retry-After` headers on 429 responses, so the hardcoded sleep provides no additional safety while significantly slowing throughput.
- **Evidence**:
  ```python
  for i in range(0, len(df), batch_size):
      batch = df.iloc[i:i + batch_size]
      results = _lookup_batch(batch[lookup_col].tolist(), api_key, config)
      enriched_data.extend(results)
      time.sleep(1)
  ```
- **Suggested fix**: Remove the fixed sleep entirely and rely on the 429 retry logic, or implement adaptive backoff based on response headers (e.g., `X-RateLimit-Remaining`). If a conservative approach is desired, make the delay configurable.
- **Effort**: Small (< 1 hour)

### [Medium] `aggregate_file` silently produces empty DataFrame when no aggregation specified
- **Category**: Correctness
- **Location**: `datamill/processor.py`, lines 34-48
- **Problem**: If neither `sum_col` nor `mean_col` is provided, the `results` dict remains empty and `pd.DataFrame(results)` returns an empty DataFrame. The CLI's `aggregate` command requires `--group-by` but makes both `--sum` and `--mean` optional, so a user can run `datamill aggregate sales.csv --group-by region` and get an empty result with no error or warning.
- **Evidence**:
  ```python
  results = {}
  if sum_col:
      results[f"{sum_col}_sum"] = grouped[sum_col].sum()
  if mean_col:
      results[f"{mean_col}_mean"] = grouped[mean_col].mean()
  return pd.DataFrame(results)
  ```
- **Suggested fix**: Check if `results` is empty before returning and either raise a `ValueError` or default to a `count()` aggregation. At minimum, print a warning.
- **Effort**: Small (< 1 hour)

### [Medium] Non-atomic cache writes can produce corrupt files
- **Category**: Correctness
- **Location**: `datamill/cache.py`, lines 49-50
- **Problem**: The `set_cached` function writes directly to the target cache file. If the process is interrupted (crash, kill signal, disk full) during the write, the cache file will contain partial/corrupt JSON. The next `get_cached` call will raise a `json.JSONDecodeError` that is not caught, crashing the caller.
- **Evidence**:
  ```python
  with open(path, "w") as f:
      json.dump(data, f)
  ```
- **Suggested fix**: Write to a temporary file in the same directory and then atomically rename it to the target path using `os.rename()`. Also add a try/except around `json.load()` in `get_cached` to handle corrupt cache files gracefully.
- **Effort**: Small (< 1 hour)

### [Medium] Race condition in cache read between `exists()` and `open()`
- **Category**: Correctness
- **Location**: `datamill/cache.py`, lines 21-26
- **Problem**: The `get_cached` function checks `path.exists()` and then opens the file in a separate step. If another process or thread deletes the cache file between these two operations, the `open()` call will raise a `FileNotFoundError` that is not caught.
- **Evidence**:
  ```python
  if not path.exists():
      return None
  with open(path) as f:
      data = json.load(f)
  ```
- **Suggested fix**: Remove the `exists()` check and wrap the `open()` in a try/except for `FileNotFoundError`, returning `None` if the file does not exist.
- **Effort**: Small (< 1 hour)

### [Low] `process` command ignores `output_format` for parquet output
- **Category**: Correctness
- **Location**: `datamill/cli.py`, lines 24-26
- **Problem**: The `process` command accepts a `--format` option that can be "csv", "json", or "parquet", but the output logic only handles "csv" (via `to_csv`) and falls through to `to_json` for everything else. If a user requests parquet output, they get JSON instead with no warning.
- **Evidence**:
  ```python
  result.to_csv(output) if fmt == "csv" else result.to_json(output)
  ```
- **Suggested fix**: Add explicit handling for "parquet" format using `result.to_parquet(output)`, and raise an error for unrecognized formats.
- **Effort**: Small (< 1 hour)

### [Low] `aggregate` command has no error handling
- **Category**: Error Handling
- **Location**: `datamill/cli.py`, lines 41-47
- **Problem**: Unlike the `process` command which wraps its logic in a try/except, the `aggregate` command has no error handling at all. Any exception (invalid file, missing column, etc.) will produce an unformatted Python traceback instead of a user-friendly error message.
- **Evidence**:
  ```python
  def aggregate(input_file, group_by, sum_col, mean_col, output):
      result = aggregate_file(input_file, group_by, sum_col=sum_col, mean_col=mean_col)
      if output:
          result.to_csv(output)
      else:
          click.echo(result.to_string())
  ```
- **Suggested fix**: Wrap the body in a try/except block consistent with the `process` command pattern.
- **Effort**: Small (< 1 hour)

### [Low] Unused imports across multiple modules
- **Category**: Maintainability
- **Location**: `datamill/processor.py` line 4 (`tempfile`); `datamill/config.py` line 4 (`json`), line 5 (`Any`); `datamill/cache.py` line 2 (`os`); `datamill/enricher.py` line 6 (`json`), line 7 (`Optional`)
- **Problem**: Several modules import names that are never used in the file. While this does not affect correctness, it clutters the code and can mislead readers about what the module actually depends on.
- **Evidence**:
  - `processor.py`: `import tempfile` -- never used
  - `config.py`: `import json` and `from typing import Any` -- never used
  - `cache.py`: `import os` -- never used (module uses `pathlib.Path` instead)
  - `enricher.py`: `import json` -- only `requests` and `response.json()` are used, not the `json` module directly; `Optional` imported but never used in type hints
- **Suggested fix**: Remove the unused imports.
- **Effort**: Small (< 1 hour)

### [Low] Cache module is never imported or used
- **Category**: Maintainability
- **Location**: `datamill/cache.py` (entire file)
- **Problem**: The `cache.py` module provides caching functions (`get_cached`, `set_cached`, `clear_cache`) but no other module in the codebase imports or uses them. The enricher module, which would benefit most from caching API responses, does not use the cache. This is dead code that adds maintenance burden.
- **Evidence**: No import of `cache` found in any other `.py` file in the project.
- **Suggested fix**: Either integrate the cache into the enricher's `_lookup_batch` function (the natural use case) or remove the module. If kept for future use, add a note explaining the intended integration point.
- **Effort**: Medium (hours) to integrate, Small (< 1 hour) to remove

### [Low] `convert_format` and `deduplicate` functions are not exposed via CLI
- **Category**: Maintainability
- **Location**: `datamill/processor.py` lines 51, 80; `datamill/cli.py`
- **Problem**: The `convert_format` and `deduplicate` functions are defined in `processor.py` but are not wired to any CLI command and not imported anywhere else in the application. They are effectively dead code from the user's perspective.
- **Evidence**: Neither function name appears in `cli.py` or any other importing module.
- **Suggested fix**: Either add CLI commands to expose these functions or remove them if they are not planned features.
- **Effort**: Small (< 1 hour) to wire up CLI commands; trivial to remove

## Issues Not Found
- **Circular dependencies**: No circular import patterns were found. The module dependency graph is clean: `cli` depends on `processor`, `enricher`, and `config`; none of these depend on each other or on `cli`.
- **Dependency vulnerabilities**: The pinned dependency versions in `pyproject.toml` (Click 8+, Pandas 2+, PyYAML 6+, Requests 2.31+) are reasonably current and no known critical CVEs apply to these minimum versions.
- **Concurrency/threading issues**: The application is single-threaded CLI tooling, so race conditions in application logic (beyond the cache file system race noted above) are not a concern.
- **N+1 query patterns**: Not applicable -- the application does not use a database ORM.
- **CORS/authentication bypass**: Not applicable -- this is a CLI tool, not a web service.
