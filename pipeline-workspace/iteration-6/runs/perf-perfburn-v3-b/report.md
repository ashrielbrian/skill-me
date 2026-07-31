I have read every source file. Here is the audit.

# Performance Audit Report

## Summary

perfburn is a telemetry service. It parses log lines, removes duplicates, adds source and region data, and writes each event to SQLite. The README states that the ingest pipeline handles about 5,000 events per second (README:10). The hot path is the loop `Pipeline.run` (perfburn/ingest/pipeline.py:61) and the per-event chain below it: `parse_line`, `Deduper.seen`, `Enricher.enrich`, `EventStore.insert_event`, and `LatencyTracker.observe`.

All numbers in this report are estimates. I did not run a profiler. I built each number from the latency table and from the counts in `perfburn/config.py`. The per-event chain costs about 1,516 µs of CPU work plus 1–10 ms of disk time. This gives about 87–400 events per second on the one worker that `ingest_workers = 1` (perfburn/config.py:22) starts. That is 12x to 57x below the documented 5,000 events per second.

The single best quick win is `Deduper.seen` (perfburn/ingest/dedupe.py:11). It scans a list of up to 50,000 strings for each event. A dict removes about 1.05 ms of work per event, and the change is about 5 lines.

## Findings

### [P1] Deduper.seen scans a list of 50,000 strings for each event

- **Category**: Algorithms
- **Location**: perfburn/ingest/dedupe.py:6, perfburn/ingest/dedupe.py:11, perfburn/ingest/dedupe.py:14
- **Hot path**: `Pipeline.process_one` calls `self._deduper.seen(event["id"])` once per event at perfburn/ingest/pipeline.py:36. `Pipeline.process_batch` calls `process_one` for each line at perfburn/ingest/pipeline.py:47. The loop `Pipeline.run` calls `process_batch` for each batch at perfburn/ingest/pipeline.py:64.
- **Problem**: `self._seen` is a list (perfburn/ingest/dedupe.py:6). The test `event_id in self._seen` is a linear scan. The capacity is `dedupe_capacity = 50000` (perfburn/config.py:17), so a steady-state miss scans all 50,000 items. The eviction `self._seen.pop(0)` at perfburn/ingest/dedupe.py:14 is also O(N). It moves 50,000 pointers on each event. Both costs grow with the capacity, so a larger dedupe window makes the pipeline slower, not safer.
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
- **Estimate**: This is an estimate. Assume 20 ns for one list element compare, which sits between an L1 reference at 0.5 ns and one interpreted statement at 10–100 ns. A miss costs 50,000 x 20 ns = 1.0 ms. The `pop(0)` call moves 50,000 pointers, which is 400 KB. One sequential read of 1 MB from memory costs 64 µs, and a memmove reads and writes, so 400 KB costs about 50 µs. The total is about 1.05 ms per event. At 5,000 events per second this needs 5.25 CPU-seconds for each second of wall time, so this step alone caps ingest at about 950 events per second. A dict gives one hash plus one main memory reference, about 50–100 ns, and O(1) eviction. The saving is about 1.05 ms per event, near 10,000x on this step, and about 5.2 CPU-seconds per second.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the list with a `dict` that maps `event_id` to `None`. A dict keeps insertion order in Python 3.7 and later, so `next(iter(d))` gives the oldest key. Use that key for the eviction, or use `collections.OrderedDict.popitem(last=False)`. The existing test `test_capacity_evicts_oldest` (tests/test_pipeline.py:41) checks the FIFO order, so keep the order. Measure events per second at the queue with a fixed input file, before and after.

### [P1] Enricher.tag_region parses the region CSV text for each event

- **Category**: Redundant Work
- **Location**: perfburn/ingest/enrich.py:21, perfburn/ingest/enrich.py:22, perfburn/ingest/enrich.py:23, perfburn/ingest/enrich.py:27
- **Hot path**: `Enricher.enrich` calls `self.tag_region(event)` at perfburn/ingest/enrich.py:35. `Pipeline.process_one` calls `self._enricher.enrich(event)` once per event at perfburn/ingest/pipeline.py:39. The loop `Pipeline.run` drives this at perfburn/ingest/pipeline.py:64.
- **Problem**: The method builds a `csv.reader` over a new `io.StringIO`, and it rebuilds the whole `regions` dict on every call. The CSV text never changes. `app.read_region_csv` reads the file one time at startup (perfburn/app.py:14) and passes the text to the constructor (perfburn/app.py:44). The code then re-parses that same text 5,000 times per second. The lookup itself uses only the first octet of the address (perfburn/ingest/enrich.py:29), so the table holds at most 256 rows.
- **Evidence**:
  ```python
  def tag_region(self, event):
      regions = {}
      reader = csv.reader(io.StringIO(self._region_csv_text))
      for row in reader:
          if len(row) == 2:
              name, prefix = row
              regions[prefix.strip()] = name.strip()
  ```
- **Estimate**: This is an estimate. The file `regions.csv` is not in the repository, so I assume 200 rows, below the 256-row limit that a first-octet key sets. One `csv.reader` row costs about 1.5 µs, so 200 rows cost 300 µs. Each row also runs 2 `strip()` calls and 1 dict store, about 4 interpreted operations at 50 ns, so 200 rows add 40 µs. The total is about 340 µs per event. At 5,000 events per second this is 1.7 CPU-seconds for each second of wall time. A 30-row table still costs about 50 µs per event, which is 0.25 CPU-seconds per second. A prebuilt dict plus the `split(".")` call costs about 200 ns. The saving is about 340 µs per event, near 1,700x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the `regions` dict one time in `Enricher.__init__` and store it as `self._regions`. Keep `tag_region` as one `split(".")` call and one dict lookup. Measure the time in `Enricher.enrich` per 100,000 events, before and after.

### [P1] LatencyTracker.observe sorts a 1024-sample window for each event

- **Category**: Redundant Work
- **Location**: perfburn/metrics.py:12, perfburn/metrics.py:15, perfburn/metrics.py:16, perfburn/metrics.py:17
- **Hot path**: The loop `Pipeline.run` calls `self._tracker.observe(len(event["fields"]))` for each event of each batch at perfburn/ingest/pipeline.py:66. The same loop reads `self._tracker.p95` one time per batch at perfburn/ingest/pipeline.py:69.
- **Problem**: `observe` sorts the full sample window on every call, but the code reads `p95` only one time per batch. The window is `sample_window = 1024` (perfburn/config.py:16). The pipeline therefore does 500 full sorts for each value that it uses, if a batch holds `batch_size = 500` lines (perfburn/config.py:8). The write and the read are decoupled, so the sort belongs on the read.
- **Evidence**:
  ```python
  def observe(self, value):
      self._samples.append(value)
      self.count += 1
      ordered = sorted(self._samples)
      index = int(len(ordered) * 0.95)
      self.p95 = ordered[min(index, len(ordered) - 1)]
  ```
- **Estimate**: This is an estimate. `sorted()` first builds a 1024-element list, which costs about 1024 x 10 ns = 10 µs. Timsort needs about 1024 x log2(1024) = 10,240 compares at 5–10 ns each, which is 50–100 µs. The total is about 60–110 µs per event; take 80 µs. At 5,000 events per second this is 0.4 CPU-seconds for each second of wall time, which is about 40% of one core. A deque append alone costs about 100 ns. If the sort moves to the read of `p95`, the rate drops from 5,000 sorts per second to 10 sorts per second, because a batch holds 500 events. The new cost is 10 x 80 µs = 0.8 ms per second. The saving is about 0.4 CPU-seconds per second, near 500x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change `observe` to append the sample and to set a dirty flag. Change `p95` to a `@property` that sorts one time when the flag is set, then clears the flag. The call site at perfburn/ingest/pipeline.py:69 does not change. Measure the time in `Pipeline.run` per 100,000 events, before and after.

### [P1] EventStore commits one transaction for each event

- **Category**: I/O and Round Trips
- **Location**: perfburn/store/db.py:20, perfburn/store/db.py:21, perfburn/store/db.py:26, perfburn/store/db.py:28, perfburn/store/db.py:29
- **Hot path**: `Pipeline.process_one` awaits `self._store.insert_event(enriched)` once per event at perfburn/ingest/pipeline.py:41. `Pipeline.process_batch` awaits `process_one` for each line in the batch at perfburn/ingest/pipeline.py:47, so the inserts run one after the other, not together.
- **Problem**: There are two costs. First, `_insert_sync` runs `self._conn.commit()` for each single row (perfburn/store/db.py:26). A commit is an fsync. WAL mode (perfburn/store/db.py:12) makes the fsync cheaper, but the default `synchronous=FULL` setting still forces one fsync of the WAL for each commit. Second, `insert_event` wraps each single row in `asyncio.to_thread` (perfburn/store/db.py:29). Each call submits work to the thread pool, creates a future, and wakes the event loop twice.
- **Evidence**:
  ```python
  def _insert_sync(self, event):
      self._conn.execute(
          "INSERT INTO events (id, day, destination, region, tags, payload)"
          " VALUES (?, ?, ?, ?, ?, ?)", ...)
      self._conn.commit()

  async def insert_event(self, event):
      await asyncio.to_thread(self._insert_sync, event)
  ```
- **Estimate**: This is an estimate. One commit or fsync costs 1–10 ms. At 5,000 events per second the code asks for 5,000 fsyncs per second, which needs 5–50 seconds of disk time for each second of wall time. One commit per item caps throughput at a few hundred items per second, so this step alone limits ingest to about 100–1,000 events per second. The `asyncio.to_thread` dispatch adds about 50–100 µs per event, which is 0.25–0.5 CPU-seconds per second at 5,000 events per second. One `executemany` call plus one commit per batch of 500 gives 10 commits per second, which costs 10–100 ms per second. The saving is 500x fewer fsyncs and 500x fewer thread dispatches.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Add a method `insert_events(rows)` that runs one `executemany` and one `commit` inside a single `asyncio.to_thread` call. Change `Pipeline.process_batch` (perfburn/ingest/pipeline.py:44) to collect the enriched events first, then to call `insert_events` one time. This change moves the durability boundary from one event to one batch of 500 events. Confirm that the trade-off is acceptable before you ship it. Measure commits per second with `PRAGMA wal_checkpoint` statistics, or count fsync calls with `dtruss`.

### [P1] Searcher.load_rows reads a whole day of events into memory

- **Category**: I/O and Round Trips
- **Location**: perfburn/query/search.py:20, perfburn/query/search.py:21, perfburn/query/search.py:23, perfburn/query/search.py:29, perfburn/query/search.py:43
- **Hot path**: `Searcher.search` calls `self.load_rows(day)` at perfburn/query/search.py:35. The README states that the query module serves a few searches per second (README:11). `app.main` builds the `Searcher` for the `query` mode at perfburn/app.py:49, and `cli.main` starts that mode at perfburn/cli.py:44.
- **Problem**: There are four separate costs in this path.
  - `load_rows` runs `SELECT * FROM events WHERE day = ?` with no LIMIT, then `fetchall()`. It builds one Python dict for every row of the day.
  - `SELECT *` returns all 6 columns, but the result at perfburn/query/search.py:42 uses only `id`, `day`, and `tags`.
  - `match_tags` calls `row["tags"].split(",")` inside the inner loop over the tags at perfburn/query/search.py:29, so the code splits the same string one time for each tag.
  - `scored.sort(reverse=True)` at perfburn/query/search.py:43 sorts every matched row, and the next line keeps only `page_size = 100` rows (perfburn/config.py:19).
- **Evidence**:
  ```python
  def load_rows(self, day):
      cur = self._conn.execute("SELECT * FROM events WHERE day = ?", (day,))
      columns = [d[0] for d in cur.description]
      return [dict(zip(columns, row)) for row in cur.fetchall()]
  ```
- **Estimate**: This is an estimate. Ingest writes 5,000 events per second, so one day holds 5,000 x 86,400 = 432,000,000 rows. One `dict(zip(columns, row))` call costs about 1 µs, because it does 6 dict stores at 50–100 ns plus the zip and the dict allocation. The total is 432,000,000 x 1 µs = 432 seconds per query, and about 216 GB of memory at 500 bytes per row. The process runs out of memory first. The repeated split adds 432,000,000 x 3 tags x 0.5 µs = 648 seconds. Even one thousandth of a day, 432,000 rows, costs about 0.4 seconds and about 200 MB. If the SQL statement filters the tags and applies a LIMIT of 100, the query reads about 100 to 1,000 rows and costs 0.5–10 ms. The `heapq.nlargest(100, scored)` call replaces the full sort: for 1,000,000 matches it costs about 10 ms instead of about 0.2 seconds.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Move the tag filter into SQL and add a LIMIT. Select only `id`, `day`, and `tags`. The index `idx_events_day` already exists (perfburn/migrate.py:12), so add a tag index or an FTS table for the tag test. Compute the split one time per row, outside the loop over the tags. Replace `scored.sort(reverse=True)` plus the slice with `heapq.nlargest(page_size, scored)`. Measure the rows that SQLite reads with `EXPLAIN QUERY PLAN` and with the query time for one day.

### [P1] Pipeline.notify blocks the event loop with a synchronous HTTP call

- **Category**: I/O and Round Trips
- **Location**: perfburn/ingest/pipeline.py:52, perfburn/ingest/pipeline.py:58, perfburn/ingest/pipeline.py:59
- **Hot path**: The loop `Pipeline.run` awaits `self.notify(event)` for each alert event at perfburn/ingest/pipeline.py:68, inside the `for event in batch` loop at perfburn/ingest/pipeline.py:65.
- **Problem**: `urllib.request.urlopen` is synchronous. The `async def` keyword does not make it yield. The call holds the event loop thread for the full request, so no other event can move through parse, dedupe, or enrich during that time. The route table sends both `error` and `warn` to `alerts` (perfburn/config.py:30, perfburn/config.py:31). `urlopen` also opens a new TCP connection for each call, so it pays a handshake every time. The calls are serial: the loop awaits one notify before it starts the next.
- **Evidence**:
  ```python
  timeout = self._config.get_int("http_timeout_s")
  urllib.request.urlopen(url, data=payload, timeout=timeout)
  ```
- **Estimate**: This is an estimate. Assume that 5% of the 5,000 events per second carry level `error` or `warn`, which gives 250 alerts per second. One same-datacenter round trip costs 50 µs, but a new connection adds a TCP handshake, and the server needs time to reply. Take 2 ms per notify. 250 x 2 ms = 0.5 seconds of blocked event loop for each second of wall time, so the loop stalls half of the time. A batch of 500 events holds about 25 alerts, so the serial notify cost is 25 x 2 ms = 50 ms per batch. The worst case is larger: `http_timeout_s = 5` (perfburn/config.py:21), so one slow endpoint stops all ingest for 5 seconds. At 5,000 events per second the queue of `queue_depth = 10000` (perfburn/config.py:12) fills in 2 seconds, and then `submit` blocks the producer. A concurrent group of 25 notifies costs about 2–4 ms instead of 50 ms.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Move the call off the event loop. The smallest change is `await asyncio.to_thread(urllib.request.urlopen, ...)`. A better change uses an async HTTP client with a shared connection pool. Collect the alert events of one batch and send them with one `asyncio.gather` call. Measure the maximum event loop lag with `loop.slow_callback_duration` and with `asyncio` debug mode.

### [P2] Enricher.lookup_source runs one SQLite query for each event on the shared connection

- **Category**: I/O and Round Trips
- **Location**: perfburn/ingest/enrich.py:13, perfburn/ingest/enrich.py:14, perfburn/app.py:44, perfburn/store/db.py:11
- **Hot path**: `Enricher.enrich` calls `self.lookup_source(...)` at perfburn/ingest/enrich.py:34. `Pipeline.process_one` calls `enrich` once per event at perfburn/ingest/pipeline.py:39.
- **Problem**: The source table is small and it changes rarely, but the code queries it for every event. There is no cache. `perfburn/config.py:24` sets `source_cache_rows` to `"0"`, and no file reads that key, so a cache was planned and it is off. A second cost comes from the connection: `app.main` passes `store.connection` to the `Enricher` (perfburn/app.py:44), and `EventStore` opens that connection with `check_same_thread=False` (perfburn/store/db.py:11). The enrich query runs on the event loop thread while `_insert_sync` uses the same connection on a worker thread. The `sqlite3` module holds a per-connection lock, so the enrich query waits behind the insert and its fsync.
- **Evidence**:
  ```python
  def lookup_source(self, source_id):
      cur = self._conn.execute(
          "SELECT name, tier FROM sources WHERE id = ?", (source_id,))
      row = cur.fetchone()
  ```
- **Estimate**: This is an estimate. `sources.id` is a PRIMARY KEY (perfburn/migrate.py:10), so the query is a b-tree probe, not a scan. A warm probe plus the row build costs about 5–15 µs; take 10 µs. At 5,000 events per second this is 50 ms for each second of wall time. A cold page adds one random 4 KB SSD read at 20 µs. The lock wait is the larger risk: while the per-event commit stands (see the P1 finding above), the enrich query can wait 1–10 ms behind one fsync. A dict cache of 1,000 sources costs one hash plus one main memory reference, about 100 ns, so it removes about 50 ms per second and it removes the lock waits from this call.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add an `lru_cache` or a plain dict on the `Enricher`, keyed by `source_id`, and size it from `source_cache_rows`. Give the `Enricher` its own `sqlite3` connection so that it does not share the lock with the insert thread. Measure the queries per second on the `sources` table with `sqlite3.Connection.set_trace_callback`.

### [P2] Enricher.enrich deep-copies each event

- **Category**: Allocation and Copies
- **Location**: perfburn/ingest/enrich.py:32, perfburn/ingest/enrich.py:33
- **Hot path**: `Pipeline.process_one` calls `self._enricher.enrich(event)` once per event at perfburn/ingest/pipeline.py:39.
- **Problem**: `copy.deepcopy` walks the object graph, keeps a memo dict, and dispatches per node. The event holds only a string `id` and a nested dict of strings (perfburn/ingest/pipeline.py:35). Strings are immutable, so a deep copy gives no extra safety here. The caller replaces `event` with `enriched` at perfburn/ingest/pipeline.py:39 and never reads the original again. A shallow rebuild of the two dicts gives the same isolation.
- **Evidence**:
  ```python
  def enrich(self, event):
      enriched = copy.deepcopy(event)
  ```
- **Estimate**: This is an estimate. `copy.deepcopy` on a 2-level dict with about 7 string fields costs about 8–15 µs; take 10 µs. At 5,000 events per second this is 50 ms for each second of wall time, which is about 5% of one core. A shallow rebuild — `dict(event)` plus `dict(event["fields"])` — costs about 0.5 µs. The saving is about 9.5 µs per event, near 20x on this step, and about 47 ms per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the deep copy with a shallow rebuild of the outer dict and of the `fields` dict. Add a comment that states that all field values are strings. Measure the time in `Enricher.enrich` per 100,000 events, before and after.

### [P2] The debug log call serializes each event even when the level is INFO

- **Category**: Observability Overhead
- **Location**: perfburn/ingest/pipeline.py:38, perfburn/config.py:13
- **Hot path**: `Pipeline.process_one` runs this line once for every accepted event at perfburn/ingest/pipeline.py:38, under the loop `Pipeline.run` at perfburn/ingest/pipeline.py:64.
- **Problem**: The default `log_level` is `"INFO"` (perfburn/config.py:13), so `logger.debug` discards the record. Python still evaluates the argument `json.dumps(event)` before it makes the call. The service therefore serializes 5,000 events per second to JSON and then throws the strings away.
- **Evidence**:
  ```python
  logger.debug("accepted event %s", json.dumps(event))
  ```
- **Estimate**: This is an estimate. `json.dumps` on a 2-level dict with about 7 fields costs about 5–8 µs. The `logger.debug` call and its level check add about 1 µs. Take 6 µs per event. At 5,000 events per second this is 30 ms for each second of wall time, which is about 3% of one core, for output that nobody reads. A guard that calls `logger.isEnabledFor` costs about 0.2 µs. The saving is about 29 ms per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the call in `if logger.isEnabledFor(logging.DEBUG):`. An alternative is a small wrapper object whose `__str__` method calls `json.dumps` only when the handler formats the record. Measure the time in `Pipeline.process_one` per 100,000 events at level INFO.

### [P2] parse_line compiles the regex and translates every character on each call

- **Category**: Redundant Work
- **Location**: perfburn/ingest/parser.py:10, perfburn/ingest/parser.py:11, perfburn/ingest/parser.py:15, perfburn/ingest/parser.py:17
- **Hot path**: `Pipeline.process_one` calls `parser.parse_line(line)` once per event at perfburn/ingest/pipeline.py:34.
- **Problem**: There are two costs. First, `re.compile(FIELD_PATTERN)` sits inside the function at perfburn/ingest/parser.py:15. The `re` module keeps an internal cache of 512 patterns, so this is a cache lookup and not a full compile. It still costs a function call, a cache key build, and a dict lookup. Second, `sanitize` calls `str.translate` with the 32-entry dict `_CONTROL` (perfburn/ingest/parser.py:7). `str.translate` with a dict does one dict lookup for every character of the line. A normal log line holds no control characters, so the whole pass produces the same string.
- **Evidence**:
  ```python
  def sanitize(raw):
      return raw.translate(_CONTROL)

  def parse_line(line):
      pattern = re.compile(FIELD_PATTERN)
  ```
- **Estimate**: This is an estimate. The cached `re.compile` call costs about 0.5–1 µs; take 0.7 µs. Assume a 100-character log line. `str.translate` with a dict does 100 lookups at 30–60 ns each, which is about 3–6 µs; take 4 µs. The total waste is about 4.7 µs per event. At 5,000 events per second this is 23 ms for each second of wall time. A module-level pattern removes the 0.7 µs. A fast path with `str.isprintable()` costs about 0.2 µs, and it is correct: all code points from 0 to 31 are in the Unicode category Cc, so `isprintable()` returns False when any of them is present. The saving is about 4.5 µs per event, which is about 22 ms per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Move the compiled pattern to module level next to `FIELD_PATTERN`. Add a fast path to `sanitize`: return `raw` when `raw.isprintable()` is True. Keep the `translate` call for the rare line that holds a control character. Measure `parse_line` over 100,000 real log lines, before and after.

### [P2] app.build_query_index scans the whole events table and nothing reads the result

- **Category**: Redundant Work
- **Location**: perfburn/app.py:22, perfburn/app.py:24, perfburn/app.py:25, perfburn/app.py:34, perfburn/app.py:52
- **Hot path**: `app.main` calls `build_query_index(conn)` at perfburn/app.py:34, before the `if mode` tests. The call runs for every mode, which includes `ingest` (perfburn/cli.py:42) and `query` (perfburn/cli.py:44).
- **Problem**: `build_query_index` runs `SELECT id, tags FROM events` with `fetchall()`, so it materializes every row of the whole table. It then builds a dict of lists. `app.main` puts the result in the wiring dict under the key `index` at perfburn/app.py:52. No other file reads that key. `Searcher.__init__` takes only `conn` (perfburn/query/search.py:17). The whole scan is waste. Startup is cold code, but this cost has no upper bound, and it delays the start of the ingest service.
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
- **Estimate**: This is an estimate. The events table grows at 5,000 rows per second, which is 432,000,000 rows per day. Each row costs one fetch, one `split(",")`, one `setdefault`, and one `append`, about 1.5 µs in total. One day of data costs 432,000,000 x 1.5 µs = 648 seconds of startup time. The `fetchall()` call and the lists of ids also hold tens of GB of memory. On an empty table the cost is near zero, so this defect appears only after the service runs. The saving is 100% of that work, because no caller reads the result.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete `build_query_index` and the `index` key from the wiring dict. Check the callers of `app.main` first, to confirm that no external caller reads `wiring["index"]`. Measure the wall time from process start to the first event that the pipeline accepts.

### [P3] Pipeline.route rebuilds the route table for each event

- **Category**: Redundant Work
- **Location**: perfburn/ingest/pipeline.py:26, perfburn/ingest/pipeline.py:27, perfburn/ingest/pipeline.py:28, perfburn/ingest/pipeline.py:29
- **Hot path**: `Pipeline.process_one` calls `self.route(enriched)` once per event at perfburn/ingest/pipeline.py:40.
- **Problem**: The method builds a new dict from `self._config.routes` on every call. The route list is static: `config.load` copies `DEFAULT_ROUTES` one time at startup (perfburn/config.py:59), and that list holds 4 entries (perfburn/config.py:29 to perfburn/config.py:34). The dict is the same on every call.
- **Evidence**:
  ```python
  def route(self, event):
      routes = {}
      for entry in self._config.routes:
          routes[entry["match"]] = entry["destination"]
  ```
- **Estimate**: This is an estimate. The build needs 1 dict allocation, 4 loop steps, 8 subscript reads, and 4 dict stores, about 12 to 17 interpreted operations at 50–100 ns each. Take 0.7–1 µs per event. At 5,000 events per second this is 3.5–5 ms for each second of wall time. A prebuilt dict lookup costs about 100 ns. The saving is about 4 ms per second, which is under 1% of the per-event path cost after the P1 fixes land.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the route map one time in `Pipeline.__init__` and store it as `self._routes`. Keep `route` as one dict lookup with the default `"events"`.

### [P3] parser.normalize builds the canonical string with repeated concatenation

- **Category**: Algorithms
- **Location**: perfburn/ingest/parser.py:23, perfburn/ingest/parser.py:24, perfburn/ingest/parser.py:25, perfburn/ingest/parser.py:26
- **Hot path**: `EventStore._insert_sync` calls `parser.normalize(event["fields"])` for the `payload` column at perfburn/store/db.py:25. That method runs once per event, under `Pipeline.process_one` at perfburn/ingest/pipeline.py:41.
- **Problem**: The loop uses `canonical += ...`. This pattern is quadratic in the general case, because each step can copy the whole string. CPython holds an in-place optimization for a string with a reference count of 1, so the real cost stays near linear for a small field count. The risk returns when a line carries many fields.
- **Evidence**:
  ```python
  canonical = ""
  for key in sorted(fields):
      canonical += key + "=" + str(fields[key]) + ";"
  ```
- **Estimate**: This is an estimate. A typical line holds about 7 fields. Each step runs 3 concatenations plus one `str()` call, about 0.3 µs, so 7 fields cost about 2 µs. The `sorted(fields)` call adds about 1 µs. At 5,000 events per second the total is 10–15 ms for each second of wall time. A `"".join(...)` version removes about 30–40% of that, which is about 4 ms per second. With 100 fields the concatenation copies about 100 x 100 / 2 = 5,000 partial strings if the in-place optimization does not apply, so the join version also removes a tail risk.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the loop with `"".join(f"{k}={fields[k]};" for k in sorted(fields))`. The test `test_normalize_is_sorted` (tests/test_pipeline.py:25) checks the exact output, so keep the trailing `;`.

### [P3] archive.compress_day reads the whole file into memory

- **Category**: Allocation and Copies
- **Location**: perfburn/store/archive.py:7, perfburn/store/archive.py:8, perfburn/store/archive.py:9, perfburn/store/archive.py:13
- **Hot path**: The scheduler calls `compress_day` one time per hour (README:13). The call is rare, but it runs in the same process as the ingest pipeline, so its memory spike hits the hot path.
- **Problem**: `f.read()` loads the whole file into one bytes object before the gzip write starts. A stream copy does the same work with a fixed buffer.
- **Evidence**:
  ```python
  with open(path, "rb") as f:
      data = f.read()
  ```
- **Estimate**: This is an estimate. At 5,000 events per second and about 200 bytes per line, one hour of data is 5,000 x 3,600 x 200 = 3.6 GB. `f.read()` therefore allocates about 3.6 GB of resident memory in the ingest process. The read itself costs about 3.6 seconds, because one sequential MB from SSD costs 1 ms; a stream copy does not change that time. The saving is the memory spike, not the time. A 3.6 GB spike can push the process into swap, or it can cause an OOM kill of the ingest service.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the read and the write with `shutil.copyfileobj(f, out)`, which uses a 64 KB buffer. Measure the peak resident memory of the process during one archive run with `resource.getrusage`.

## Cold-Code Notes

- perfburn/query/reportgen.py:51 to perfburn/query/reportgen.py:54 — `render` calls `summarize(conn, day)` 4 times, so the same `GROUP BY destination, region` query runs 4 times over one day of rows, about 432,000,000 rows. Call `summarize` one time and pass the result to the 4 render functions. This runs once per day via cron (README:12).
- perfburn/migrate.py:37 — `conn.commit()` sits inside the row loop. One fsync per row at 1–10 ms caps the migration at 100–1,000 rows per second. Move the commit outside the loop, or use `executemany`.
- perfburn/migrate.py:23 — `cur.fetchall()` loads every legacy row into memory before the insert loop starts. Iterate the cursor instead.
- perfburn/config.py:56 — `load` compiles 20 regexes and runs 20 `MULTILINE` searches over the whole config text. That is 20 scans of the file where one pass over the lines answers all 20 keys.
- perfburn/cli.py:14 to perfburn/cli.py:20 — `resolve_alias` runs a nested loop over 4 commands, rebuilds the list `[command] + aliases` on each step, and calls `.lower()` on both sides for each candidate. A prebuilt dict makes this one lookup.
- perfburn/app.py:14 — `read_region_csv` reads the whole region file into one string at startup. This is correct as written, because the enricher needs the whole table.
- perfburn/store/archive.py:19 — `prune` sorts the full `os.listdir` result to drop the oldest entries. The directory is small, so the sort is acceptable.
- tests/test_pipeline.py:11 to tests/test_pipeline.py:15 — `make_events` builds a 10,000-line blob with `blob += ...`. The CPython in-place optimization keeps this near linear, so the test is not slow. A list plus `"".join` removes the risk.
- perfburn/config.py:8 to perfburn/config.py:26 — 9 config keys have no reader in the package: `batch_size`, `flush_interval_ms`, `retention_days`, `report_sections`, `ingest_workers`, `day_format`, `source_cache_rows`, `alert_level`, and `listen_port`. This is not a performance cost, but it means that the real batch size comes from the producer, not from the config.

## Not Found

- **Data Layout**: No finding. There is no map that a dense integer array replaces, no set of small integers that a bit vector replaces, and no nested map of maps. `Searcher.load_rows` builds one dict per row (perfburn/query/search.py:23), which the P1 search finding covers.
- **Algorithms, nested loops**: No O(N²) loop over two collections. `Searcher.match_tags` (perfburn/query/search.py:25) is O(rows x tags), but the tag count is small; the row count is the defect, and the P1 search finding covers it.
- **Observability Overhead, beyond the debug log**: `EventStore.queue_metric` (perfburn/store/db.py:31) already batches at `metric_flush_rows = 200` rows. The pipeline calls it one time per batch (perfburn/ingest/pipeline.py:69), so it writes one time per 100,000 events at a batch size of 500. This is a correct design; do not change it. `LatencyTracker.snapshot` (perfburn/metrics.py:19) has no caller in the package, but an uncalled method costs nothing.
- **Caches that already exist**: `score_term` carries `lru_cache(maxsize=4096)` (perfburn/query/search.py:8). The author already handled this path.
- **Missing indexes**: `idx_events_day` exists (perfburn/migrate.py:12), so both `Searcher.load_rows` and `reportgen.summarize` use an index for the day filter. The defect in the search path is the number of rows that the query returns, not a missing index.
- **Backpressure**: `asyncio.Queue(maxsize=queue_depth)` (perfburn/ingest/pipeline.py:20) with `queue_depth = 10000` is correct backpressure. No finding.
- **Dismissed after a check**: `Deduper.size()` (perfburn/ingest/dedupe.py:17) calls `len()` on a list, which is O(1). The `re.compile` call in `parse_line` costs about 0.7 µs, not the about 20 µs of a full compile, because the `re` module caches 512 patterns; I kept it inside a P2 and not a P1 for that reason. `parser.normalize` stays near linear at 7 fields, about 2 µs, so I kept it at P3 and not at P1.