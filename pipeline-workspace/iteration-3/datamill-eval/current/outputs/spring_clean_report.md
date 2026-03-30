# Codebase Audit Report

## Summary

Datamill is a Python CLI tool for processing, filtering, aggregating, and enriching CSV/JSON datasets. It uses Click for CLI, Pandas for data manipulation, and makes external API calls for data enrichment. The codebase has several critical security vulnerabilities -- most urgently, arbitrary code execution via `eval()` on user-supplied filter expressions, shell injection in format conversion, a hardcoded production API key committed to source, and API credentials leaked to logs. The project is relatively small (6 source files) and appears to be in early active development (v0.2.0, single initial commit), with minimal test coverage.

## Findings

### [Critical] Arbitrary code execution via `eval()` on user-supplied filter expression

- **Category**: Security
- **Location**: `datamill/processor.py`, line 16
- **Problem**: The `process_file` function passes the user-supplied `--filter` argument directly to Python's `eval()`. Although `__builtins__` is set to an empty dict, this is trivially bypassable and allows arbitrary code execution on the host machine. Any user who can invoke the CLI can run arbitrary Python code.
- **Evidence**:
  ```python
  df = df[eval(filter_expr, {"__builtins__": {}}, df.to_dict("list"))]
  ```
  Setting `__builtins__` to `{}` does not create a secure sandbox. Attackers can escape via `().__class__.__bases__[0].__subclasses__()` and similar techniques to import `os` and execute shell commands.
- **Suggested fix**: Replace `eval()` with Pandas' built-in `DataFrame.query()` method, which supports a safe subset of expressions, or implement a custom expression parser that only allows column comparisons.
- **Effort**: Medium (hours)

### [Critical] Shell injection via `subprocess.run()` with unsanitized user input

- **Category**: Security
- **Location**: `datamill/processor.py`, lines 63-64
- **Problem**: The `convert_format` function constructs a shell command by interpolating user-controlled `target_format`, `input_path`, and `output_path` values directly into a string passed to `subprocess.run()` with `shell=True`. An attacker can inject arbitrary shell commands through crafted filenames or format strings (e.g., `; rm -rf /`).
- **Evidence**:
  ```python
  subprocess.run(f"csvtool --format {target_format} {input_path} > {output_path}",
                 shell=True, check=True)
  ```
- **Suggested fix**: Use `subprocess.run()` with a list of arguments instead of a shell string, avoid `shell=True`, and validate `target_format` against an allowlist of supported formats. Use `shlex.quote()` as a secondary defense if shell invocation is unavoidable.
- **Effort**: Small (< 1 hour)

### [Critical] Hardcoded production API key in source code

- **Category**: Security
- **Location**: `datamill/config.py`, line 9 and lines 37-42
- **Problem**: A production API key (`dk_prod_a8f3b2c1d4e5f6789012345678901234`) is hardcoded as a constant and used as a fallback when no API key is provided via config or environment. This key is committed to version control, meaning anyone with repository access (including public access if the repo is ever open-sourced) has the production key. The `get_api_key` function silently falls back to it, so users may unknowingly make requests with a shared production credential.
- **Evidence**:
  ```python
  FALLBACK_API_KEY = "dk_prod_a8f3b2c1d4e5f6789012345678901234"
  ```
- **Suggested fix**: Remove the hardcoded key entirely. Rotate the exposed key immediately. Require the API key to be explicitly provided via environment variable or config file, and raise a clear error if it is missing. If the key has been committed to a public or shared repo, treat it as compromised.
- **Effort**: Small (< 1 hour)

### [High] API key leaked in log output

- **Category**: Security
- **Location**: `datamill/enricher.py`, line 23
- **Problem**: The full API key is logged at INFO level in plaintext whenever `enrich_file` is called. In production environments, INFO-level logs are typically shipped to centralized logging systems (ELK, Datadog, CloudWatch, etc.) where they are stored, indexed, and accessible to a wide audience. This leaks the credential to anyone with log access.
- **Evidence**:
  ```python
  logger.info(f"Starting enrichment with API key: {api_key}")
  ```
- **Suggested fix**: Remove the API key from the log message entirely, or at most log a masked version (e.g., the last 4 characters). Review other logging statements in the file for similar issues -- the remaining log statements in this file (`logger.info`, `logger.warning`, `logger.error` on lines 24, 51, 56) do not leak secrets.
- **Effort**: Small (< 1 hour)

### [High] Unsafe YAML loading allows arbitrary code execution

- **Category**: Security
- **Location**: `datamill/config.py`, line 22
- **Problem**: `yaml.FullLoader` is used instead of `yaml.SafeLoader`. While `FullLoader` is safer than the completely unrestricted `yaml.UnsafeLoader`, it still allows instantiation of arbitrary Python objects via YAML tags, which can lead to code execution if a malicious config file is provided. Since config file paths can be user-supplied via the `--config` CLI option, this is exploitable.
- **Evidence**:
  ```python
  config = yaml.load(f, Loader=yaml.FullLoader)
  ```
- **Suggested fix**: Replace `yaml.FullLoader` with `yaml.SafeLoader`, or use `yaml.safe_load(f)` which is equivalent and more readable.
- **Effort**: Small (< 1 hour)

### [High] HTTP used instead of HTTPS for API communication

- **Category**: Security
- **Location**: `datamill/enricher.py`, line 12; `datamill/config.py`, line 32
- **Problem**: The default API endpoint uses `http://` instead of `https://`. All API requests, including the `Authorization: Bearer` header containing the API key, are sent in plaintext over the network. Any network intermediary (proxy, ISP, attacker on the same network) can intercept the API key and all data being enriched.
- **Evidence**:
  ```python
  DEFAULT_API_URL = "http://api.enrichment-service.com/v2/lookup"
  ```
  and in `_default_config()`:
  ```python
  "api_url": "http://api.enrichment-service.com/v2/lookup",
  ```
- **Suggested fix**: Change both URLs from `http://` to `https://`. Consider adding a validation check that rejects non-HTTPS URLs in production.
- **Effort**: Small (< 1 hour)

### [High] `_load_file` returns `None` for unsupported file extensions

- **Category**: Error Handling
- **Location**: `datamill/processor.py`, lines 21-29
- **Problem**: The `_load_file` function has no `else` clause for unrecognized file extensions. It implicitly returns `None`, which causes confusing `AttributeError` exceptions downstream when callers try to operate on the result (e.g., `df.groupby()`, `df.to_csv()`). This affects `process_file`, `aggregate_file`, and `convert_format` -- all of which call `_load_file`.
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
      # No else clause -- returns None
  ```
- **Suggested fix**: Add an `else` clause that raises a `ValueError` with a clear message listing the supported formats.
- **Effort**: Small (< 1 hour)

### [High] Unbounded recursive retry on HTTP 429 can cause stack overflow

- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, lines 49-53
- **Problem**: When the API returns a 429 (rate limited) response, `_lookup_batch` recursively calls itself with no depth limit. If the API continues to return 429 (e.g., because the account is suspended, or the rate limit window is long), this will recurse until Python's default recursion limit (~1000) is hit, causing a `RecursionError` and losing all enrichment progress.
- **Evidence**:
  ```python
  elif response.status_code == 429:
      retry_after = int(response.headers.get("Retry-After", 5))
      logger.warning(f"Rate limited. Retrying after {retry_after}s")
      time.sleep(retry_after)
      return _lookup_batch(queries, api_key, config)
  ```
- **Suggested fix**: Convert to an iterative retry loop with a configurable maximum retry count (e.g., 3-5 attempts). Raise an exception after exhausting retries so the caller can handle it.
- **Effort**: Small (< 1 hour)

### [Medium] No timeout on HTTP requests to external API

- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, line 44
- **Problem**: The `requests.post()` call has no `timeout` parameter. If the enrichment API is unresponsive, the request will block indefinitely, hanging the CLI process with no way to recover. In a batch processing context, this could stall an entire pipeline.
- **Evidence**:
  ```python
  response = requests.post(url, json={"queries": queries}, headers=headers)
  ```
- **Suggested fix**: Add a `timeout` parameter, e.g., `timeout=(5, 30)` for 5-second connect timeout and 30-second read timeout. Make the timeout configurable through the config dict.
- **Effort**: Small (< 1 hour)

### [Medium] API errors silently return empty results instead of failing

- **Category**: Error Handling
- **Location**: `datamill/enricher.py`, lines 55-57
- **Problem**: When the API returns a non-200, non-429 status code (e.g., 500 Internal Server Error, 401 Unauthorized, 403 Forbidden), the function logs the error but returns an empty list. The caller proceeds as if the batch had no matches. This means the final merged DataFrame will have `NaN` values for all enrichment columns on affected rows, with no indication to the user that data is incomplete. A persistent API error would silently produce a fully un-enriched output.
- **Evidence**:
  ```python
  else:
      logger.error(f"API error: {response.status_code}")
      return []
  ```
- **Suggested fix**: Raise an exception on non-retryable errors (4xx other than 429, 5xx after retries) so the caller can decide whether to abort or continue with partial results. At minimum, surface a warning to the user through the CLI.
- **Effort**: Medium (hours)

### [Medium] `aggregate_file` silently produces empty DataFrame when no aggregation is specified

- **Category**: Correctness
- **Location**: `datamill/processor.py`, lines 34-48
- **Problem**: If a user calls `aggregate` without specifying `--sum` or `--mean`, the function returns an empty DataFrame with no error or warning. The CLI prints an empty table, which is confusing. The `aggregate` CLI command requires `--group-by` but does not require at least one aggregation function.
- **Evidence**:
  ```python
  results = {}
  if sum_col:
      results[f"{sum_col}_sum"] = grouped[sum_col].sum()
  if mean_col:
      results[f"{mean_col}_mean"] = grouped[mean_col].mean()
  return pd.DataFrame(results)  # empty if neither provided
  ```
- **Suggested fix**: Validate that at least one of `sum_col` or `mean_col` is provided. Raise a `ValueError` or `click.UsageError` if neither is specified.
- **Effort**: Small (< 1 hour)

### [Medium] Non-atomic cache writes can produce corrupted files

- **Category**: Correctness
- **Location**: `datamill/cache.py`, lines 49-50
- **Problem**: `set_cached` writes directly to the target file. If the process crashes or is killed during the write, the cache file will be partially written (truncated JSON), causing `json.load()` to fail with a `JSONDecodeError` on the next read. This would effectively break caching for that key until the corrupted file is manually deleted.
- **Evidence**:
  ```python
  with open(path, "w") as f:
      json.dump(data, f)
  ```
- **Suggested fix**: Write to a temporary file in the same directory, then atomically rename it to the target path. For example, use `tempfile.NamedTemporaryFile(dir=CACHE_DIR, delete=False)` followed by `os.replace(tmp.name, path)`.
- **Effort**: Small (< 1 hour)

### [Medium] Cache uses MD5 hashing which has collision vulnerabilities

- **Category**: Security
- **Location**: `datamill/cache.py`, line 14
- **Problem**: MD5 is used to hash cache keys into filenames. While the collision risk is low for a local file cache and there is no direct security exploit here, MD5 is a broken hash function. If cache keys are influenced by external input, a deliberate collision could cause one query's cached result to be returned for a different query, leading to incorrect enrichment data.
- **Evidence**:
  ```python
  hashed = hashlib.md5(key.encode()).hexdigest()
  ```
- **Suggested fix**: Replace `hashlib.md5` with `hashlib.sha256` for a more robust hash. The performance difference is negligible for short strings.
- **Effort**: Small (< 1 hour)

### [Low] Missing error handling in `aggregate` CLI command

- **Category**: Error Handling
- **Location**: `datamill/cli.py`, lines 41-47
- **Problem**: The `process` command wraps its logic in a try/except block and reports errors cleanly, but the `aggregate` command has no error handling. If the input file is missing, the group-by column does not exist, or `_load_file` returns `None`, the user sees a raw Python traceback instead of a friendly error message.
- **Evidence**:
  ```python
  def aggregate(input_file, group_by, sum_col, mean_col, output):
      """Aggregate a dataset by grouping."""
      result = aggregate_file(input_file, group_by, sum_col=sum_col, mean_col=mean_col)
      # No try/except -- raw exceptions propagate
  ```
- **Suggested fix**: Add the same try/except pattern used in the `process` command.
- **Effort**: Small (< 1 hour)

### [Low] Missing error handling in `enrich` CLI command

- **Category**: Error Handling
- **Location**: `datamill/cli.py`, lines 55-60
- **Problem**: Similar to the `aggregate` command, the `enrich` command has no error handling. Network failures, missing files, or API errors will produce raw tracebacks.
- **Evidence**:
  ```python
  def enrich(input_file, api_key, output, config_path):
      """Enrich a dataset using an external API."""
      config = load_config(config_path) if config_path else {}
      result = enrich_file(input_file, api_key, config=config)
      # No try/except
  ```
- **Suggested fix**: Add a try/except block as in the `process` command. Consider extracting the error handling into a shared decorator.
- **Effort**: Small (< 1 hour)

### [Low] Race condition between `path.exists()` and `open()` in cache read

- **Category**: Correctness
- **Location**: `datamill/cache.py`, lines 21-25
- **Problem**: There is a TOCTOU (time-of-check-time-of-use) race between `path.exists()` and `open(path)`. If another process deletes the cache file between these two calls, a `FileNotFoundError` will be raised. In practice, this is unlikely in a CLI tool that is typically single-process, but it would cause a crash rather than a graceful cache miss.
- **Evidence**:
  ```python
  if not path.exists():
      return None
  with open(path) as f:
      data = json.load(f)
  ```
- **Suggested fix**: Remove the `exists()` check and wrap the `open()` in a try/except `FileNotFoundError` block that returns `None`.
- **Effort**: Small (< 1 hour)

### [Low] Unused imports

- **Category**: Maintainability
- **Location**: `datamill/processor.py`, line 4 (`tempfile`); `datamill/enricher.py`, lines 6-7 (`json`, `Optional`); `datamill/config.py`, lines 4-5 (`json`, `Any`)
- **Problem**: Several modules are imported but never used in their respective files. This is minor but adds clutter and can mislead developers into thinking the imports are needed.
- **Evidence**: `tempfile` is imported in `processor.py` but never referenced. `json` and `Optional` are imported in `enricher.py` but not used. `json` and `Any` are imported in `config.py` but not used.
- **Suggested fix**: Remove unused imports. Consider adding a linter (e.g., `ruff` or `flake8`) to catch these automatically.
- **Effort**: Small (< 1 hour)

### [Low] `_validate_api_key` is defined but never called

- **Category**: Maintainability
- **Location**: `datamill/enricher.py`, lines 60-67
- **Problem**: The `_validate_api_key` function exists but is never called anywhere in the codebase. The `enrich_file` function and the CLI `enrich` command both accept API keys without validation. This means invalid API keys are sent to the API, wasting a network request before discovering the key is bad.
- **Evidence**: Grep for `_validate_api_key` shows only its definition, no call sites.
- **Suggested fix**: Call `_validate_api_key` at the start of `enrich_file` (or in the CLI `enrich` command) and raise an error if the key is invalid. Alternatively, remove the dead code if validation is intentionally deferred to the API.
- **Effort**: Small (< 1 hour)

### [Low] `process` command ignores `--format` flag when writing to file for non-CSV formats

- **Category**: Correctness
- **Location**: `datamill/cli.py`, lines 26-27
- **Problem**: The `process` command only distinguishes between "csv" and everything else (treated as JSON) when writing output. The `--format parquet` option is advertised in the help text but is not handled -- it would silently produce a JSON file instead of a Parquet file.
- **Evidence**:
  ```python
  result.to_csv(output) if fmt == "csv" else result.to_json(output)
  ```
- **Suggested fix**: Add explicit handling for all supported formats (csv, json, parquet) with appropriate `to_*` methods, and raise an error for unsupported formats.
- **Effort**: Small (< 1 hour)

## Issues Not Found

- **Correctness -- Race conditions in concurrent code**: The codebase is single-threaded with no use of threads, async, or multiprocessing, so concurrent data races are not applicable (aside from the minor filesystem TOCTOU noted above).
- **Security -- SQL injection or path traversal**: The codebase does not use SQL databases, and file paths are handled through standard library functions without manual path construction from user input.
- **Security -- CORS or authentication/authorization**: This is a CLI tool, not a web server, so web-specific security concerns do not apply.
- **Performance -- N+1 queries or missing indexes**: No database is used; data processing is done in-memory with Pandas.
- **Maintainability -- Circular dependencies**: The module dependency graph is clean and acyclic (cli -> processor, enricher, config; no reverse dependencies).
- **Maintainability -- Duplicated business logic**: No significant code duplication was found; the codebase is small enough that logic is not yet duplicated.
