# Codebase Audit Report

## Summary
Datamill is a Python CLI tool for processing, filtering, aggregating, and enriching CSV/JSON/Parquet datasets, built with Click, Pandas, and Requests. The codebase has several critical security vulnerabilities -- including arbitrary code execution via `eval()`, shell injection, and hardcoded production API keys -- alongside multiple error handling gaps and correctness issues. Overall, the project is a small but functional tool at an early stage (v0.2.0) with minimal test coverage and significant security risks that must be resolved before any production use.

## Findings

### [Critical] Arbitrary code execution via eval() on user-supplied filter expression
- **Category**: Security
- **Location**: `datamill/processor.py`, line 16
- **Problem**: The `process_file` function passes a user-supplied `filter_expr` string directly to Python's `eval()`. Although `__builtins__` is set to an empty dict, this is trivially bypassable and allows arbitrary code execution. An attacker can craft a filter expression that imports modules, reads files, or executes system commands.
- **Evidence**:
  ```python
  df = df[eval(filter_expr, {"__builtins__": {}}, df.to_dict("list"))]
  ```
- **Suggested fix**: Replace `eval()` with `pandas.DataFrame.query()`, which provides a safe DSL for filtering DataFrames, or implement a purpose-built expression parser that only supports comparison operators on column names.
- **Effort**: Small (< 1 hour)

### [Critical] Shell injection via user-controlled format string in subprocess call
- **Category**: Security
- **Location**: `datamill/processor.py`, lines 63-64
- **Problem**: The `convert_format` function constructs a shell command using unsanitized user input (`target_format` and `input_path`). An attacker can inject arbitrary shell commands through either parameter (e.g., `target_format="csv; rm -rf /"` or a malicious filename).
- **Evidence**:
  ```python
  subprocess.run(f"csvtool --format {target_format} {input_path} > {output_path}",
                 shell=True, check=True)
  ```
- **Suggested fix**: Use `subprocess.run` with a list of arguments instead of `shell=True`, and validate `target_format` against an allowlist of supported formats. Use `shlex.quote()` if shell invocation is truly necessary.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded production API key in source code
- **Category**: Security
- **Location**: `datamill/config.py`, lines 9 and 42
- **Problem**: A production API key is hardcoded as a constant (`FALLBACK_API_KEY`) and used as a fallback when no key is provided via config or environment. This key will be committed to version control, visible to anyone with repository access, and difficult to rotate.
- **Evidence**:
  ```python
  FALLBACK_API_KEY = "dk_prod_a8f3b2c1d4e5f6789012345678901234"
  ```
  and:
  ```python
  if not key:
      return FALLBACK_API_KEY
  ```
- **Suggested fix**: Remove the hardcoded key entirely. Require the API key to be provided via environment variable or config file, and raise an error with a helpful message if it is missing.
- **Effort**: Small (< 1 hour)

### [High] API key leaked via logging
- **Category**: Security
- **Location**: `datamill/enricher.py`, line 23
- **Problem**: The enrichment API key is logged at INFO level, meaning it will appear in log files, log aggregation systems, and potentially stdout. This exposes the credential to anyone with log access.
- **Evidence**:
  ```python
  logger.info(f"Starting enrichment with API key: {api_key}")
  ```
- **Suggested fix**: Remove the API key from the log message entirely. If confirmation of key presence is needed, log only a masked version (e.g., the last 4 characters).
- **Effort**: Small (< 1 hour)

### [High] Unsafe YAML loading allows arbitrary code execution
- **Category**: Security
- **Location**: `datamill/config.py`, line 22
- **Problem**: `yaml.FullLoader` is used instead of `yaml.SafeLoader`. While `FullLoader` is safer than the bare `yaml.load()` without a Loader, it still allows construction of arbitrary Python objects from certain YAML tags, which can lead to code execution if the config file is attacker-controlled or tampered with.
- **Evidence**:
  ```python
  config = yaml.load(f, Loader=yaml.FullLoader)
  ```
- **Suggested fix**: Use `yaml.safe_load(f)` or `yaml.load(f, Loader=yaml.SafeLoader)`, which only allows basic YAML types.
- **Effort**: Small (< 1 hour)

### [High] Default API endpoint uses HTTP instead of HTTPS
- **Category**: Security
- **Location**: `datamill/enricher.py`, line 12; `datamill/config.py`, line 33
- **Problem**: The default enrichment API URL uses plain HTTP, meaning API keys and data are transmitted in cleartext and vulnerable to interception or man-in-the-middle attacks. This URL appears in both the enricher module and the default config.
- **Evidence**:
  ```python
  DEFAULT_API_URL = "http://api.enrichment-service.com/v2/lookup"
  ```
  and in `_default_config()`:
  ```python
  "api_url": "http://api.enrichment-service.com/v2/lookup",
  ```
- **Suggested fix**: Change both URLs to use `https://`.
- **Effort**: Small (< 1 hour)

### [High] _load_file returns None implicitly for unknown file extensions
- **Category**: Correctness
- **Location**: `datamill/processor.py`, lines 21-29
- **Problem**: The `_load_file` function handles `.csv`, `.json`, and `.parquet` extensions but has no `else` clause. For any other file extension, the function returns `None` implicitly, which causes `AttributeError` downstream when callers attempt to call DataFrame methods on `None`.
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
      # No else clause - returns None
  ```
- **Suggested fix**: Add an `else` clause that raises a `ValueError` with a descriptive message listing supported formats.
- **Effort**: Small (< 1 hour)

### [High] Unbounded recursive retry on HTTP 429 can cause stack overflow
- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, lines 49-53
- **Problem**: When the enrichment API returns a 429 (rate limited) response, `_lookup_batch` calls itself recursively with no depth limit. If the API continuously returns 429, this will exceed Python's default recursion limit (typically 1000) and crash with a `RecursionError`.
- **Evidence**:
  ```python
  elif response.status_code == 429:
      retry_after = int(response.headers.get("Retry-After", 5))
      logger.warning(f"Rate limited. Retrying after {retry_after}s")
      time.sleep(retry_after)
      return _lookup_batch(queries, api_key, config)
  ```
- **Suggested fix**: Convert to an iterative retry loop with a configurable maximum number of attempts (e.g., 3-5 retries), and raise an exception when the limit is exceeded.
- **Effort**: Small (< 1 hour)

### [Medium] No timeout on HTTP requests to enrichment API
- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, line 44
- **Problem**: The `requests.post()` call has no `timeout` parameter. If the enrichment API becomes unresponsive, the request will block indefinitely, hanging the entire CLI process.
- **Evidence**:
  ```python
  response = requests.post(url, json={"queries": queries}, headers=headers)
  ```
- **Suggested fix**: Add a `timeout` parameter, e.g., `timeout=(5, 30)` for a 5-second connect timeout and 30-second read timeout.
- **Effort**: Small (< 1 hour)

### [Medium] API errors silently return empty results instead of raising
- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, lines 55-57
- **Problem**: When the enrichment API returns a non-200/non-429 status code, the function logs an error but returns an empty list. The caller has no indication that the enrichment failed, and the final merged DataFrame will have null values in enrichment columns without any user-facing warning.
- **Evidence**:
  ```python
  else:
      logger.error(f"API error: {response.status_code}")
      return []
  ```
- **Suggested fix**: Raise an exception for server errors (5xx) so the caller can handle the failure. For 4xx client errors, provide a clear error message to the user.
- **Effort**: Small (< 1 hour)

### [Medium] aggregate_file silently returns empty DataFrame when no aggregation specified
- **Category**: Correctness
- **Location**: `datamill/processor.py`, lines 40-48
- **Problem**: If neither `sum_col` nor `mean_col` is provided, the function returns an empty DataFrame with no useful data and no error. The user gets no indication that they forgot to specify an aggregation.
- **Evidence**:
  ```python
  results = {}
  if sum_col:
      results[f"{sum_col}_sum"] = grouped[sum_col].sum()
  if mean_col:
      results[f"{mean_col}_mean"] = grouped[mean_col].mean()
  return pd.DataFrame(results)
  ```
- **Suggested fix**: Raise a `ValueError` if neither `sum_col` nor `mean_col` is provided, or default to counting rows per group.
- **Effort**: Small (< 1 hour)

### [Medium] Fixed sleep between API batches ignores rate limit headers
- **Category**: Performance
- **Location**: `datamill/enricher.py`, line 32
- **Problem**: A hardcoded 1-second sleep is used between every batch of API calls, regardless of the API's actual rate limit headers. For large datasets, this adds unnecessary latency (e.g., 100 batches = 100 seconds of idle waiting). Conversely, if the API's rate limit is stricter than 1 request/second, this could still trigger rate limiting.
- **Evidence**:
  ```python
  time.sleep(1)
  ```
- **Suggested fix**: Check for `Retry-After` or `X-RateLimit-*` headers in the response and use adaptive backoff. Remove the fixed sleep if the API does not require rate limiting.
- **Effort**: Small (< 1 hour)

### [Medium] Non-atomic cache writes risk corrupted cache files
- **Category**: Correctness
- **Location**: `datamill/cache.py`, lines 49-50
- **Problem**: Cache files are written directly without an atomic write pattern. If the process crashes during the write, the cache file will be partially written and contain invalid JSON, causing `json.JSONDecodeError` on the next read attempt for that key.
- **Evidence**:
  ```python
  with open(path, "w") as f:
      json.dump(data, f)
  ```
- **Suggested fix**: Write to a temporary file in the same directory, then atomically rename it to the target path using `os.rename()`.
- **Effort**: Small (< 1 hour)

### [Low] Race condition between file existence check and file open in cache
- **Category**: Correctness
- **Location**: `datamill/cache.py`, lines 21-26
- **Problem**: There is a TOCTOU (time-of-check-time-of-use) race between `path.exists()` and `open(path)`. Another process could delete the file between these two calls, causing a `FileNotFoundError`.
- **Evidence**:
  ```python
  if not path.exists():
      return None
  with open(path) as f:
      data = json.load(f)
  ```
- **Suggested fix**: Use a try/except around the `open()` call, catching `FileNotFoundError` and returning `None`.
- **Effort**: Small (< 1 hour)

### [Low] Aggregate command in CLI has no error handling
- **Category**: Error Handling
- **Location**: `datamill/cli.py`, lines 41-47
- **Problem**: The `aggregate` CLI command does not wrap its logic in a try/except block, unlike the `process` command. Any exception (e.g., missing column, unsupported file type) will produce an unformatted Python traceback instead of a clean error message.
- **Evidence**:
  ```python
  def aggregate(input_file, group_by, sum_col, mean_col, output):
      """Aggregate a dataset by grouping."""
      result = aggregate_file(input_file, group_by, sum_col=sum_col, mean_col=mean_col)
      if output:
          result.to_csv(output)
      else:
          click.echo(result.to_string())
  ```
- **Suggested fix**: Add try/except handling consistent with the `process` command, printing a user-friendly error and exiting with code 1.
- **Effort**: Small (< 1 hour)

### [Low] MD5 used for cache key hashing
- **Category**: Security
- **Location**: `datamill/cache.py`, line 14
- **Problem**: MD5 is used to hash cache keys into filenames. While this is not a security-critical use (cache keys are not adversarial in typical usage), MD5 is cryptographically broken and susceptible to collision attacks. If cache keys ever incorporate user-controlled data, an attacker could craft collisions to poison cache entries.
- **Evidence**:
  ```python
  hashed = hashlib.md5(key.encode()).hexdigest()
  ```
- **Suggested fix**: Replace with `hashlib.sha256()` for a stronger hash, or keep MD5 if cache key collision is an accepted risk in this context.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Circular dependencies**: The module dependency graph is straightforward (cli -> processor, enricher, config; enricher uses requests; cache is standalone). No circular imports.
- **Dead code**: All defined functions appear to have call sites or are part of the public API. No commented-out code blocks or unreachable branches were found beyond the issues already reported.
- **N+1 queries / database concerns**: The project does not use a database; data is processed in-memory via Pandas.
- **Concurrency issues beyond cache**: The tool runs as a single-threaded CLI process. The cache race condition noted above is the only concurrency concern.
- **Dependency vulnerabilities**: The dependencies (click, pandas, pyyaml, requests) are specified with reasonable version floors. No known-vulnerable version ranges were identified from the `pyproject.toml` constraints.
