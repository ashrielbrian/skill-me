I have read every source file. Now I will write the report.

# Performance Audit Report

## Summary

`perfburn` parses telemetry log lines, removes duplicates, adds source and region data, and writes rows to SQLite. The hot path is `Pipeline.process_one` at `perfburn/ingest/pipeline.py:33`. The README states this path runs about 5,000 times per second. One event loop thread carries this work (`asyncio.run` at `perfburn/app.py:62`), so the budget is 200 µs of CPU time per event.

Five separate defects each cost more than that whole budget. The best quick win replaces the list in `Deduper` with a set and a `collections.deque` (`perfburn/ingest/dedupe.py:6-15`). That change is about 5 lines and it removes about 1.25 ms of CPU work per event. The hardest limit is the commit per event at `perfburn/store/db.py:26`, which caps the pipeline at about 200 events per second.

**Anchor for all estimates below**: the target rate is 5,000 events per second on one thread. One core supplies 1,000 ms of CPU time per second. A cost of 200 µs per event uses one full core.

## Findings

### [P1] `Deduper.seen` scans a list of 50,000 ids for each event

- **Category**: Algorithms
- **Location**: `perfburn/ingest/dedupe.py:9-15`; capacity default at `perfburn/config.py:22`
- **Hot path**: `Pipeline.process_one` calls `self._deduper.seen(event["id"])` at `perfburn/ingest/pipeline.py:36`. `process_batch` calls `process_one` once per line at `perfburn/ingest/pipeline.py:47`. `run` calls `process_batch` once per batch at `perfburn/ingest/pipeline.py:64`. The rate is 5,000 events per second.
- **Problem**: `self._seen` is a Python list. The test `event_id in self._seen` is a linear scan. The `dedupe_capacity` default is 50,000, so the list holds 50,000 ids in the steady state. The list reaches capacity after 50,000 events, which is 10 seconds at the target rate. Duplicates are rare, so most calls are a miss, and a miss scans all 50,000 entries. The method also calls `self._seen.pop(0)`, which moves every remaining pointer down one slot.
- **Evidence**:
  ```python
  def seen(self, event_id):
      if event_id in self._seen:
          return True
      self._seen.append(event_id)
      if len(self._seen) > self._capacity:
          self._seen.pop(0)
      return False
  ```
- **Estimate**: This is an estimate. The scan touches 50,000 list slots and calls a string compare on each one. The pointer array is 50,000 × 8 bytes = 400 KB, which is larger than a typical L2 cache, and each string object sits in separate heap memory. Use 25 ns per element, between the 0.5 ns L1 reference and the 50 ns main memory reference. The scan costs 50,000 × 25 ns = 1.25 ms per event. `pop(0)` moves 400 KB, which costs about 25 µs at the 64 µs per MB memory rate. The total is about 1.28 ms per event, which is 6 times the whole 200 µs budget. At 5,000 events per second the cost is 6,400 ms of CPU time per second, or 6.4 cores. A set membership test costs about 50 ns and a `deque` append with `maxlen` costs about 50 ns. The saving is about 1.28 ms per event, or 6.4 cores. That is a factor of about 12,000 on this operation.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Hold the ids in a `set` for the test and in a `collections.deque(maxlen=capacity)` for the order. On each evict, remove the popped id from the set. Measure the CPU time of `seen` with `cProfile` before the change and after it. Confirm that `Deduper.size()` still reports the same count, and keep the eviction test at `tests/test_pipeline.py:41-46` green.

### [P1] `EventStore._insert_sync` commits once per event

- **Category**: I/O and Round Trips
- **Location**: `perfburn/store/db.py:20-29`
- **Hot path**: `Pipeline.process_one` awaits `self._store.insert_event(enriched)` at `perfburn/ingest/pipeline.py:41`, once per line. `process_batch` runs that call in a sequential loop at `perfburn/ingest/pipeline.py:45-49`.
- **Problem**: `_insert_sync` runs one `INSERT` and then calls `self._conn.commit()`. The code sets `PRAGMA journal_mode=WAL` at `perfburn/store/db.py:12` but it does not set `PRAGMA synchronous`. The SQLite default is `synchronous=FULL`, so each commit calls fsync on the write-ahead log. The pipeline therefore asks the disk for 5,000 fsync operations per second. `insert_event` also sends each row through `asyncio.to_thread`, which costs one thread pool dispatch and one event loop round trip for each event.
- **Evidence**:
  ```python
  def _insert_sync(self, event):
      self._conn.execute(
          "INSERT INTO events (id, day, destination, region, tags, payload)"
          " VALUES (?, ?, ?, ?, ?, ?)",
          (event["id"], event["day"], event["destination"], event["region"],
           event["fields"].get("tags", ""), parser.normalize(event["fields"])))
      self._conn.commit()

  async def insert_event(self, event):
      await asyncio.to_thread(self._insert_sync, event)
  ```
- **Estimate**: This is an estimate. One commit costs one fsync, which is 1 ms to 10 ms. Use 5 ms. The disk therefore supports about 200 commits per second. One commit per event caps the pipeline at about 200 events per second, which is 25 times below the documented 5,000 events per second. The `asyncio.to_thread` dispatch costs about 50 µs to 100 µs per event. At 5,000 events per second that overhead alone is 250 ms to 500 ms per second, or 0.25 to 0.5 cores. The `batch_size` default is 500 (`perfburn/config.py:8`), so one `executemany` and one commit per batch gives 10 commits per second at the target rate. The fsync cost drops from 25,000 ms per second to 50 ms per second, a factor of 500. The thread dispatch count drops from 5,000 per second to 10 per second.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `_insert_many_sync(events)` that calls `executemany` once and then commits once. Change `process_one` so that it returns the enriched event and does not insert it. Add one `await self._store.insert_events(results)` call at the end of `process_batch`. State the trade-off to the operators: a crash can now lose up to one batch of 500 events. As a second, smaller step, set `PRAGMA synchronous=NORMAL`, which is safe in WAL mode against a process crash but not against a power loss. Measure the commit count per second and the end-to-end events per second. Note one related cost: `flush_metrics` at `perfburn/store/db.py:36-43` commits on the event loop thread once per 200 batches, which stalls the loop for about 5 ms every 20 seconds. That cost is small, but the same batched insert work can move it to a thread.

### [P1] `Enricher.tag_region` parses the region CSV text again for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/enrich.py:21-30`
- **Hot path**: `Enricher.enrich` calls `self.tag_region(event)` at `perfburn/ingest/enrich.py:35`. `Pipeline.process_one` calls `self._enricher.enrich(event)` at `perfburn/ingest/pipeline.py:39`, once per event. `app.main` reads the CSV file one time at `perfburn/app.py:44` and passes the text to the constructor, so the text never changes after startup.
- **Problem**: The method builds a fresh `io.StringIO`, a fresh `csv.reader`, and a fresh `regions` dict on every call. It then uses one lookup from that dict and throws the dict away. The map depends only on `self._region_csv_text`, which is constant for the life of the process.
- **Evidence**:
  ```python
  def tag_region(self, event):
      regions = {}
      reader = csv.reader(io.StringIO(self._region_csv_text))
      for row in reader:
          if len(row) == 2:
              name, prefix = row
              regions[prefix.strip()] = name.strip()
      address = event["fields"].get("ip", "0.0.0.0")
      octet = address.split(".")[0]
      return regions.get(octet, "other")
  ```
- **Estimate**: This is an estimate. The code keys the map by the first octet of an IP address, so the file holds at most 255 useful rows. Assume 200 rows. Each row costs one loop step, one `len` call, one tuple unpack, two `strip` calls, and one dict store. That is about 6 interpreted operations at 50 ns each, plus the C parse work in `csv`. Use 1.5 µs per row. The parse costs 200 × 1.5 µs = 300 µs per event. Add about 2 µs for the `StringIO` object and the `csv.reader` object. The total of 302 µs per event is above the whole 200 µs budget. At 5,000 events per second the cost is 1,510 ms per second, or 1.5 cores. Rescale this number if the file is smaller: 20 rows costs 30 µs per event, which is still 150 ms per second. After the fix the method costs one dict lookup and one `split` call, about 0.5 µs. The saving is about 300 µs per event, a factor of about 600 on this method.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the `regions` dict one time in `Enricher.__init__` and store it on the instance. Keep `tag_region` as the octet lookup only. Measure the CPU time of `enrich` with `cProfile` before and after. Check that an empty CSV text still gives the `"other"` result, because `read_region_csv` returns `""` when the file is absent (`perfburn/app.py:18-19`).

### [P1] `Pipeline.notify` makes a blocking HTTP call inside the event loop

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/pipeline.py:52-59`
- **Hot path**: `Pipeline.run` awaits `self.notify(event)` for each event with destination `alerts` at `perfburn/ingest/pipeline.py:67-68`. The route table sends `level=error` and `level=warn` to `alerts` (`perfburn/config.py:30-31`).
- **Problem**: This defect appears only when an operator sets `webhook_url`. The `webhook_url` default is `""` (`perfburn/config.py:10`), so the method returns at `perfburn/ingest/pipeline.py:55` and costs nothing in the default configuration. When the URL is set, `urllib.request.urlopen` is a synchronous call inside an `async def` function. The call blocks the whole event loop thread. Every other coroutine stops for the length of the HTTP request: the queue read, the parse work, and the inserts. `urlopen` also opens a new TCP connection on each call and does not reuse a session. The `run` loop awaits the calls one after another, so it does not overlap them.
- **Evidence**:
  ```python
  async def notify(self, event):
      url = self._config.get("webhook_url")
      if not url:
          return
      payload = json.dumps(
          {"id": event["id"], "destination": event["destination"]}).encode()
      timeout = self._config.get_int("http_timeout_s")
      urllib.request.urlopen(url, data=payload, timeout=timeout)
  ```
- **Estimate**: This is an estimate. Assume 10 percent of the 5,000 events per second carry `level=error` or `level=warn`. That gives 500 webhook calls per second. A same-datacenter round trip is 50 µs, but `urlopen` adds a TCP connect, an optional TLS handshake, and the server work. Use 20 ms per call. The stall is 500 × 20 ms = 10,000 ms of blocked event loop time per second. The loop cannot keep up, and the throughput falls to about 50 events per second, which is one alert every 20 ms. The `http_timeout_s` default is 5 seconds (`perfburn/config.py:21`), so one slow endpoint stalls the whole pipeline for 5 seconds. After the fix the alert work runs off the loop thread and costs the loop about 5 µs per alert for the dispatch. The saving is the full 10,000 ms per second of stall.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the `urlopen` call with `await asyncio.to_thread(...)`. Collect the alert events for one batch and send them with `asyncio.gather`, so the calls overlap. Reuse one `urllib.request.OpenerDirector` or one HTTP session across calls. Measure the event loop lag with `asyncio` debug mode, and measure the events per second with a webhook set.

### [P1] `Searcher.search` loads a whole day of rows and filters them in Python

- **Category**: I/O and Round Trips
- **Location**: `perfburn/query/search.py:20-44`
- **Hot path**: `Searcher.search` runs once per user query. The README states the query module serves a few searches per second. `app.main` builds the `Searcher` at `perfburn/app.py:49`.
- **Problem**: `load_rows` runs `SELECT * FROM events WHERE day = ?` and calls `fetchall()`. It then builds one dict per row. `match_tags` walks every row in Python and calls `row["tags"].split(",")` inside the inner loop over tags, so it splits the same string once per tag. `search` then sorts every matched row and returns only the first 100. Three further costs add to this. The `SELECT *` reads all 6 columns, but the code uses only `id`, `day`, and `tags` (`perfburn/query/search.py:42`); the unused `payload` column holds the largest value. The schema has an index on `day` only, and no index on `tags` (`perfburn/migrate.py:12`). The database does none of the tag filter work.
- **Evidence**:
  ```python
  def load_rows(self, day):
      cur = self._conn.execute("SELECT * FROM events WHERE day = ?", (day,))
      columns = [d[0] for d in cur.description]
      return [dict(zip(columns, row)) for row in cur.fetchall()]

  def match_tags(self, rows, tags):
      matched = []
      for row in rows:
          for tag in tags:
              if tag in row["tags"].split(","):
                  matched.append(row)
                  break
      return matched
  ```
- **Estimate**: This is an estimate. Assume 3 tags per query and a page size of 100 (`perfburn/config.py:19`). Two scales follow. At the documented 5,000 events per second, one day holds 5,000 × 86,400 = 432,000,000 rows. At about 200 bytes per row that is 86 GB. A read of 86 GB from SSD at 1 ms per MB costs 86 seconds. The 432,000,000 dict builds at 1 µs each cost 432 seconds, and the 1,296,000,000 `split` calls at 0.3 µs each cost 389 seconds. `fetchall()` also holds every row in memory at once, so the process runs out of memory first. At a conservative 1,000,000 rows per day the numbers are: 200 MB read at 1 ms per MB = 0.2 s; 1,000,000 dict builds = 1.0 s; 3,000,000 `split` calls = 0.9 s; one full sort of the matched rows at about 100 ns per tuple compare = up to 2.0 s. The total is about 4 seconds per query. At 3 queries per second the module needs 12 cores. After the fix the database probes an index and returns 100 rows, which costs about 1 ms. The saving is a factor of about 4,000 at the 1,000,000-row scale.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Move the tag filter into SQL. Store the tags in a separate `event_tags` table with one row per (event id, tag) pair, and index the tag column. Then run one query with a `WHERE tag IN (...) AND day = ?` clause and a `LIMIT`. Select only `id`, `day`, and `tags`; do not select `payload`. Replace `scored.sort(reverse=True)` with `heapq.nlargest(page_size, scored)`. Split `row["tags"]` one time per row, outside the loop over tags. Profile the query first, because the schema change is the largest part of the work. Measure the rows read per query and the query latency at the 95th percentile.

### [P2] `LatencyTracker.observe` sorts the whole sample window for each event

- **Category**: Observability Overhead
- **Location**: `perfburn/metrics.py:12-17`
- **Hot path**: `Pipeline.run` calls `self._tracker.observe(len(event["fields"]))` once per event at `perfburn/ingest/pipeline.py:66`. `Pipeline.run` reads `self._tracker.p95` one time per batch at `perfburn/ingest/pipeline.py:69`.
- **Problem**: `observe` sorts the whole window on every call and stores the result in `self.p95`. The pipeline reads `p95` once per batch, not once per event. With the `batch_size` default of 500, the code discards 499 of every 500 sort results. The `sample_window` default is 1,024 (`perfburn/config.py:16`).
- **Evidence**:
  ```python
  def observe(self, value):
      self._samples.append(value)
      self.count += 1
      ordered = sorted(self._samples)
      index = int(len(ordered) * 0.95)
      self.p95 = ordered[min(index, len(ordered) - 1)]
  ```
- **Estimate**: This is an estimate. `sorted()` first copies the 1,024-item `deque` into a list, which costs about 20 ns per element, or 20 µs. The observed value is `len(event["fields"])`, the field count of the line. A fixed log format gives a nearly constant field count, so Timsort finds one long run and does about 1,023 comparisons at 20 ns, or 20 µs. The best case is therefore about 40 µs per event. If the field count varies, Timsort does about 1,024 × log2(1,024) = 10,240 comparisons, which raises the cost to about 225 µs per event. At 5,000 events per second the range is 200 ms per second (0.2 cores) to 1,125 ms per second (1.1 cores). Use the 40 µs figure, because the real data is nearly constant. A compute-on-read design does 10 sorts per second instead of 5,000, which cuts the work by a factor of 500. The saving is about 200 ms per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Move the sort out of `observe`. Make `p95` a property that sorts on demand, or add a `flush()` method that the batch loop calls one time. Keep `observe` as an append and a counter increment. The result stays exact. Measure the CPU time of `observe` with `cProfile`. One further note: the tracker holds field counts, not latencies, so the metric name `batch_p95` at `perfburn/ingest/pipeline.py:69` does not match the data. Confirm the intent before you change the code.

### [P2] `Enricher.enrich` deep-copies the event

- **Category**: Allocation and Copies
- **Location**: `perfburn/ingest/enrich.py:33`
- **Hot path**: `Pipeline.process_one` calls `self._enricher.enrich(event)` at `perfburn/ingest/pipeline.py:39`, once per event at 5,000 events per second.
- **Problem**: `copy.deepcopy` walks the whole object graph. It builds a memo dict, calls `id()` on each node, stores each node in the memo, and dispatches through a type table. The event holds only strings, and Python strings are immutable, so a shallow copy gives the same protection at a small fraction of the cost.
- **Evidence**:
  ```python
  def enrich(self, event):
      enriched = copy.deepcopy(event)
  ```
  The event shape comes from `perfburn/ingest/pipeline.py:35`: `event = {"id": fields.get("id", ""), "fields": fields}`. All values in `fields` are strings from `parse_line` (`perfburn/ingest/parser.py:19`).
- **Estimate**: This is an estimate. The graph holds 2 dicts and about 7 string values, which is 9 nodes. `copy.deepcopy` costs 20 to 100 times a shallow copy on small structures. Use 15 µs per event. A shallow form, `{**event, "fields": dict(event["fields"])}`, costs about 0.5 µs. The saving is 14.5 µs per event, which is 7 percent of the 200 µs budget. At 5,000 events per second the saving is 72 ms per second, or 7 percent of one core. That is a factor of 30 on this call.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the `deepcopy` call with `{**event, "fields": dict(event["fields"])}`. Drop the `import copy` line. Confirm first that no caller mutates a nested value in place. Measure the CPU time of `enrich`.

### [P2] `Enricher.lookup_source` runs one SQLite query for each event

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/enrich.py:13-19`
- **Hot path**: `Enricher.enrich` calls `self.lookup_source(...)` at `perfburn/ingest/enrich.py:34`. `Pipeline.process_one` calls `enrich` at `perfburn/ingest/pipeline.py:39`, once per event.
- **Problem**: The method queries the `sources` table for every event. SQLite runs in the same process, so there is no network round trip, but each call still costs a Python call, a statement cache lookup, a B-tree probe, and a `fetchone` call. The `sources` table is small and it changes rarely: it holds `id`, `name`, and `tier` only (`perfburn/migrate.py:9-10`), and the test data uses 5 source ids (`tests/test_pipeline.py:13`). The config holds a `source_cache_rows` key set to `"0"` (`perfburn/config.py:24`), but no code reads that key, so the intended cache does not exist. A second cost adds to this. `app.main` passes `store.connection` to the `Enricher` at `perfburn/app.py:44`, so the enricher and `_insert_sync` share one `sqlite3` connection. `_insert_sync` runs in a worker thread through `asyncio.to_thread` (`perfburn/store/db.py:29`). SQLite serializes the two users of that connection with an internal mutex, so the query on the event loop thread can wait for a write in the worker thread.
- **Evidence**:
  ```python
  def lookup_source(self, source_id):
      cur = self._conn.execute(
          "SELECT name, tier FROM sources WHERE id = ?", (source_id,))
      row = cur.fetchone()
  ```
- **Estimate**: This is an estimate. One indexed point query through the Python `sqlite3` layer costs about 10 µs, which covers the call overhead, the statement cache lookup, the primary key probe, and the row build. At 5,000 events per second the cost is 50 ms per second, or 5 percent of one core. A dict lookup costs about 50 ns, so a preloaded map saves about 9.95 µs per event, or about 50 ms per second. That is a factor of 200 on this call. The mutex wait is separate: a mutex lock and unlock is 15 ns, but a wait behind a 5 ms commit is 5 ms. Finding P1 on the commit per event removes most of those waits.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Load the `sources` table into a dict one time in `Enricher.__init__`. Add a reload method if the table can change while the process runs; otherwise state the assumption in a comment. Give the `Enricher` its own read-only connection instead of the write connection from the store. Measure the CPU time of `lookup_source` and the events per second.

### [P2] `Pipeline.process_one` formats a debug log argument at every log level

- **Category**: Observability Overhead
- **Location**: `perfburn/ingest/pipeline.py:38`
- **Hot path**: `Pipeline.process_one` runs once per event at 5,000 events per second, through `process_batch` at `perfburn/ingest/pipeline.py:47`.
- **Problem**: Python evaluates the arguments before it calls `logger.debug`. `json.dumps(event)` therefore runs at every log level. The `log_level` default is `INFO` (`perfburn/config.py:13`), so the logger discards the string immediately. The lazy `%s` form protects the string interpolation but not the `json.dumps` call.
- **Evidence**:
  ```python
  logger.debug("accepted event %s", json.dumps(event))
  ```
- **Estimate**: This is an estimate. `json.dumps` on a 2-key dict that holds a 6-key dict costs about 5 µs. The discarded `logger.debug` call costs about 1 µs for the level check and the record path. The total is about 6 µs per event. At 5,000 events per second the waste is 30 ms per second, or 3 percent of one core. The whole cost disappears when the level is above DEBUG. The saving is 30 ms per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Guard the call with `if logger.isEnabledFor(logging.DEBUG):`. As an alternative, pass `event` as the argument and let a custom formatter serialize it. Note also that no code reads the `log_level` config key, so the logger level comes from the `logging` defaults. Wire that key to `logging.basicConfig`. Measure the CPU time of `process_one`.

### [P2] `build_query_index` scans the whole events table and no code reads the result

- **Category**: Redundant Work
- **Location**: `perfburn/app.py:22-28`; the call site is `perfburn/app.py:34`
- **Hot path**: `app.main` calls `build_query_index(conn)` before it checks the mode, so both the `ingest` path and the `query` path pay the cost. `cli.main` calls `app.main` on each `perfburn ingest` invocation and on each `perfburn query` invocation (`perfburn/cli.py:42-44`). This is a startup path, but the CLI repeats it on each invocation.
- **Problem**: The function runs `SELECT id, tags FROM events`, calls `fetchall()`, and builds a dict of lists in memory. `app.main` returns that dict under the `index` key at `perfburn/app.py:52`. No other code reads `wiring["index"]`. A search over the tree finds the name `index` only in `app.py` and as an unrelated local variable in `perfburn/metrics.py:16`. The whole scan is therefore dead work. The `perfburn query` command pays the cost and then runs no search at all: `cli.main` discards the return value of `app.main` at `perfburn/cli.py:43`.
- **Evidence**:
  ```python
  def build_query_index(conn):
      index = {}
      cur = conn.execute("SELECT id, tags FROM events")
      for event_id, tags in cur.fetchall():
          for tag in (tags or "").split(","):
              index.setdefault(tag, []).append(event_id)
      return index
  ```
- **Estimate**: This is an estimate. The `retention_days` default is 30 (`perfburn/config.py:17`), so the table holds up to 30 days of events. At a conservative 1,000,000 rows the scan reads about 200 MB from SSD at 1 ms per MB, which costs 0.2 s. Each row then costs one tuple build, one `split` call, one `setdefault` call, and one `append` call, about 1.5 µs, which gives 1.5 s. The total is about 1.7 s per invocation, and the dict holds about 150 MB. At the documented rate of 5,000 events per second, one day alone holds 432,000,000 rows. The scan then reads about 65 GB and holds every row in memory, so the process runs out of memory and fails. The fix removes the whole cost.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete `build_query_index` and the `index` key from the wiring dict. If a tag index is a planned feature, build it in the database instead, as finding P1 on `Searcher.search` describes. Measure the wall clock time of `perfburn query` before the change and after it.

### [P3] `parser.sanitize` maps every character through a dict

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/parser.py:7-11`
- **Hot path**: `parse_line` calls `sanitize(line)` at `perfburn/ingest/parser.py:17`. `Pipeline.process_one` calls `parser.parse_line(line)` at `perfburn/ingest/pipeline.py:34`, once per event at 5,000 events per second.
- **Problem**: `str.translate` with a dict does one mapping lookup for each code point in the string. Most log lines hold no control characters, so the call builds a new string that equals the input. The module-level `_CONTROL` table is correct and the author already hoisted it, so only the fast path is absent.
- **Evidence**:
  ```python
  _CONTROL = {i: None for i in range(32)}

  def sanitize(raw):
      return raw.translate(_CONTROL)
  ```
- **Estimate**: This is an estimate. Assume a log line of 100 characters. The `translate` call does 100 dict lookups at about 40 ns each, which is 4 µs, plus one string allocation. `str.isprintable()` is a C-level scan and costs about 0.3 µs for 100 characters. A fast path saves about 3.7 µs per event. At 5,000 events per second the saving is 18 ms per second, or 1.8 percent of one core.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Return `raw` unchanged when `raw.isprintable()` is true. Call `translate` only on the rare line that fails that test. Add a test for a line that holds a control character, so the slow path stays covered.

### [P3] `Pipeline.route` rebuilds the route map for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/pipeline.py:26-31`
- **Hot path**: `Pipeline.process_one` calls `self.route(enriched)` at `perfburn/ingest/pipeline.py:40`, once per event at 5,000 events per second.
- **Problem**: The method walks `self._config.routes` and builds a dict on every call. `Config.load` sets `routes` one time at startup from `DEFAULT_ROUTES` (`perfburn/config.py:59`), and no code changes the list afterward. The map is therefore constant.
- **Evidence**:
  ```python
  def route(self, event):
      routes = {}
      for entry in self._config.routes:
          routes[entry["match"]] = entry["destination"]
      level = event["fields"].get("level", "info")
      return routes.get(level, "events")
  ```
- **Estimate**: This is an estimate. `DEFAULT_ROUTES` holds 4 entries (`perfburn/config.py:29-34`). Each entry costs one loop step, two dict reads, and one dict store, about 200 ns. The loop costs 800 ns, and the dict allocation costs about 100 ns. The total is about 1 µs per event. At 5,000 events per second the waste is 5 ms per second, or 0.5 percent of one core.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the map one time in `Pipeline.__init__` and store it as `self._routes`. Keep `route` as the level lookup only.

### [P3] `parse_line` calls `re.compile` on every event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/parser.py:14-15`
- **Hot path**: `Pipeline.process_one` calls `parser.parse_line(line)` at `perfburn/ingest/pipeline.py:34`, once per event at 5,000 events per second.
- **Problem**: The function calls `re.compile(FIELD_PATTERN)` inside the body. The `re` module holds a cache of compiled patterns, so the second call and all later calls hit that cache. The cost is therefore a dict lookup and a function call, not a full compile. This is a much smaller defect than the pattern looks.
- **Evidence**:
  ```python
  def parse_line(line):
      pattern = re.compile(FIELD_PATTERN)
  ```
- **Estimate**: This is an estimate. A full compile of this pattern costs 50 µs to 200 µs, but it happens one time only. The cached path costs about 1 µs per call: the `re.compile` call, the cache key build from the pattern and the flags, and the dict lookup. A module-level compiled pattern costs 0 per call. At 5,000 events per second the saving is 5 ms per second, or 0.5 percent of one core.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `FIELD_RE = re.compile(FIELD_PATTERN)` at module level and use it in `parse_line`. Keep `FIELD_PATTERN` as the source string, because the module exports it.

### [P3] `parser.normalize` builds the string with `+=` in a loop

- **Category**: Allocation and Copies
- **Location**: `perfburn/ingest/parser.py:23-27`
- **Hot path**: `EventStore._insert_sync` calls `parser.normalize(event["fields"])` at `perfburn/store/db.py:25`, once per event at 5,000 events per second.
- **Problem**: The loop appends to `canonical` with `+=`. CPython resizes the string in place when the reference count is 1, so the quadratic cost does not appear at the current field count of about 6. The pattern is still a risk: a line with 100 fields makes 100 appends over a growing string, and any code that holds a second reference to `canonical` disables the in-place path. `sorted(fields)` also allocates a new list on every call.
- **Evidence**:
  ```python
  def normalize(fields):
      canonical = ""
      for key in sorted(fields):
          canonical += key + "=" + str(fields[key]) + ";"
      return canonical
  ```
- **Estimate**: This is an estimate. With 6 fields the loop does 6 appends and 18 small concatenations, about 1.5 µs, plus about 0.5 µs for the `sorted` list. A `"".join()` form with a generator saves about 1 µs per event. At 5,000 events per second the saving is 5 ms per second, or 0.5 percent of one core. The saving grows with the field count: at 100 fields per line the join form removes the quadratic risk.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the loop with `"".join(f"{k}={fields[k]};" for k in sorted(fields))`. Keep the sort, because the test at `tests/test_pipeline.py:25-27` requires the sorted order and the output is a canonical form.

## Cold-Code Notes

- `perfburn/query/reportgen.py:51-54` — `render` calls `summarize(conn, day)` 4 times, so it runs 4 identical `GROUP BY` queries where 1 result serves all 4 sections. Cron runs `render` once per day.
- `perfburn/query/reportgen.py:20-21` — `top_events` sorts every row to return the top 10; `heapq.nlargest(10, ...)` does the same work without the full sort. Once per day.
- `perfburn/store/archive.py:8-9` — `compress_day` reads the whole file into memory with `f.read()` before it compresses; `shutil.copyfileobj` streams it in fixed blocks. The scheduler runs this once per hour, and the database file can reach many GB.
- `perfburn/config.py:55-58` — `load` compiles 20 regular expressions and scans the whole config text 20 times, once for each key in `DEFAULTS`. One pass over the lines answers all 20 keys. Once at startup.
- `perfburn/migrate.py:33-38` — the loop commits once per legacy row, so a 1,000,000-row migration costs 1,000,000 fsync operations, about 5,000 s at 5 ms each. One commit after the loop is enough. This is a one-shot script that runs manually.
- `perfburn/cli.py:16-19` — `resolve_alias` builds a new candidate list for each command and calls `.lower()` on each candidate inside the loop. A module-level dict that maps every alias to its command replaces the whole scan. Once per invocation.
- `tests/test_pipeline.py:9-15` — `make_events` builds a 10,000-line blob with `+=` in a loop, and `test_bulk_parse` parses all 10,000 lines. The total is about 0.2 s, which does not block CI.
- `perfburn/config.py:8-26` — 12 config keys have defaults but no reader: `batch_size`, `flush_interval_ms`, `log_level`, `archive_dir`, `retention_days`, `search_page_size`, `report_sections`, `ingest_workers`, `day_format`, `source_cache_rows`, `alert_level`, and `listen_port`. This costs no run time, but it hides two intended optimizations: the batch size for the inserts and the cache for the sources table.

## Not Found

- **Data Layout** — no finding. The hot path holds no map keyed by small dense integers, no set of small integers, and no nested map that costs several lookups per access. The dict-per-row shape at `perfburn/query/search.py:23` is a real per-row cost, but finding P1 on `Searcher.search` covers it, because the fix removes those rows from Python entirely.
- **Algorithms** — one finding only, on `Deduper.seen`. I checked the other candidates and dismissed them. `score_term` at `perfburn/query/search.py:8-13` already has an `lru_cache` with 4,096 entries, and the function body costs about 0.3 µs for a short tag, so the cache is enough. `summarize` at `perfburn/query/reportgen.py:5-7` pushes the aggregation into SQL with a `GROUP BY` clause, which is correct. `archive.prune` at `perfburn/store/archive.py:19` sorts a directory listing once per hour, which is a few hundred entries at most.
- **Observability Overhead, metric writes** — no finding. `EventStore.queue_metric` at `perfburn/store/db.py:31-34` already buffers the metric rows and flushes them with one `executemany` every 200 rows. `Pipeline.run` calls `queue_metric` once per batch at `perfburn/ingest/pipeline.py:69`, not once per event. At the target rate the flush costs one 5 ms commit every 20 seconds, which is 0.025 percent of the event loop time. The author got this right.
- **Allocation and Copies, preallocation** — no finding beyond the `deepcopy` call. The append-grow loops in the hot path are bounded by the field count of about 6, so a preallocation saves under 1 µs per event.