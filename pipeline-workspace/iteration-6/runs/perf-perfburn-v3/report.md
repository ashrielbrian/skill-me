I read all 15 source files (570 lines). Python execution needs approval in this session, so every Estimate below is an analytical estimate that uses the given latency table plus standard CPython per-operation costs, with the assumptions stated.

# Performance Audit Report

## Summary

`perfburn` parses telemetry log lines, removes duplicates, adds source and region data, and writes rows to SQLite. The hot path is `Pipeline.run` at `perfburn/ingest/pipeline.py:61`, which handles about 5,000 events per second (README:10). That rate gives a budget of 200 µs of CPU per event on one core. The single best quick win is the duplicate check at `perfburn/ingest/dedupe.py:11`. It scans a list of 50,000 ids per event and costs about 1,050 µs per event, more than 5 times the whole budget. A set plus a deque removes almost all of that cost in about 10 lines.

## Findings

### [P1] Deduper scans a 50,000-element list for each event

- **Category**: Algorithms
- **Location**: `perfburn/ingest/dedupe.py:9-15`
- **Hot path**: `Pipeline.process_one` calls `self._deduper.seen(event["id"])` at `perfburn/ingest/pipeline.py:36`. `process_batch` calls `process_one` for each line at `perfburn/ingest/pipeline.py:47`. `run` calls `process_batch` for each queue item at `perfburn/ingest/pipeline.py:64`. The rate is about 5,000 events per second (README:10).
- **Problem**: `self._seen` is a list. The membership test `event_id in self._seen` is a linear scan. The capacity default is 50,000 (`perfburn/config.py:17`), so a new id compares against all 50,000 entries. `self._seen.pop(0)` then moves every remaining element one slot to the left. Both costs grow with the capacity value.
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
- **Estimate**: This is an estimate. Assumptions: the list holds 50,000 ids; one `PyObject_RichCompare` step inside the C scan costs 20 ns; the ids differ in the first characters.
  - The scan for a new id costs 50,000 × 20 ns = 1,000 µs.
  - `pop(0)` moves 49,999 pointers of 8 bytes. That is 400 KB read plus 400 KB written. At 64 µs per 1 MB from memory, that is about 50 µs.
  - Total: about 1,050 µs per event. The budget is 200 µs per event, so one event needs 5.25 times the whole budget.
  - At 5,000 events per second, the pipeline needs 5.25 s of CPU for each 1 s of wall time. The service cannot reach the documented rate.
  - A duplicate id stops the scan early only when it sits near the front. An average duplicate costs 25,000 comparisons, about 500 µs.
  - After the fix: one hash plus one set probe is about 50 ns. `deque.popleft` is about 50 ns. Total about 0.15 µs. The saving is about 1,050 µs per event, a factor of about 7,000.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Hold the ids in a `set` for the membership test. Hold the same ids in a `collections.deque` for the eviction order. On overflow, call `popleft()` and discard that id from the set. Keep the first-in, first-out eviction order. The test at `tests/test_pipeline.py:41` fixes that order, so do not switch to random eviction. Measure the events per second that one process sustains, before the change and after it.

### [P1] Enricher re-parses the whole region CSV text for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/enrich.py:21-30`
- **Hot path**: `Enricher.enrich` calls `self.tag_region(event)` at `perfburn/ingest/enrich.py:35`. `Pipeline.process_one` calls `self._enricher.enrich(event)` at `perfburn/ingest/pipeline.py:39`, once per event, about 5,000 times per second (README:10).
- **Problem**: `tag_region` builds a new `io.StringIO` object, a new `csv.reader` object, and a new `regions` dict on every call. The source text never changes. `app.read_region_csv` reads the file one time at startup and `app.main` passes the text to the constructor at `perfburn/app.py:44`. The method then uses one dict entry and discards all the others.
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
- **Estimate**: This is an estimate. Assumption: the file holds 256 rows. The code keys the map by the first IP octet, so 256 is the natural upper bound for the table.
  - Setup per call: one `io.StringIO` object about 1 µs, one `csv.reader` object about 2 µs.
  - Each row runs one `len(row) == 2` test, one tuple unpack, two `str.strip()` calls, and one dict store. That is about 6 interpreted operations at about 80 ns each, so about 0.5 µs per row.
  - 256 rows × 0.5 µs = 128 µs, plus 3 µs of setup = about 131 µs per event.
  - At 5,000 events per second: 655 ms of CPU for each 1 s of wall time, about 65% of one core.
  - A smaller file still costs a lot. With 20 rows the cost is 20 × 0.5 + 3 = 13 µs per event, so 65 ms per second.
  - After the fix, the per-event work is one `split(".")` about 0.4 µs plus one dict lookup about 0.05 µs, so about 0.5 µs. The saving is about 130 µs per event and about 650 ms per second.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Parse the CSV text one time in `Enricher.__init__` and store the `regions` dict on the instance. Keep `tag_region` limited to the `split(".")` call and the dict lookup. Measure the CPU time of the ingest process for a fixed number of events, before the change and after it.

### [P1] LatencyTracker sorts the whole 1024-sample window for each event

- **Category**: Redundant Work
- **Location**: `perfburn/metrics.py:12-17`
- **Hot path**: `Pipeline.run` calls `self._tracker.observe(...)` for each event of each batch at `perfburn/ingest/pipeline.py:66`, about 5,000 times per second (README:10). `run` reads the result once per batch at `perfburn/ingest/pipeline.py:69`.
- **Problem**: `observe` sorts all samples on every call to update `self.p95`. The window default is 1024 (`perfburn/config.py:16`). The pipeline reads `p95` one time per batch, and the batch size default is 500 (`perfburn/config.py:8`). So the code runs 500 sorts for each 1 value that a caller reads. The sort result is a full order, but the code uses only one element of it.
- **Evidence**:
```python
    def observe(self, value):
        self._samples.append(value)
        self.count += 1
        ordered = sorted(self._samples)
        index = int(len(ordered) * 0.95)
        self.p95 = ordered[min(index, len(ordered) - 1)]
```
- **Estimate**: This is an estimate. Assumptions: the window holds 1024 integers; the specialized integer comparison inside the sort costs 2.5 ns.
  - `sorted` first copies 1024 pointers from the deque into a new list: about 4 µs.
  - Timsort runs about 1024 × log2(1024) = 10,240 comparisons: about 26 µs.
  - Total: about 30 µs per event. That is 15% of the 200 µs budget per event.
  - At 5,000 events per second: 150 ms of CPU for each 1 s of wall time.
  - The three other P1 fixes above cut the per-event CPU to about 62 µs. `observe` then holds about 30 µs of that 62 µs, about 48%. It becomes the largest single consumer of CPU on the path.
  - After the fix, the sort count falls from 5,000 per second to 10 per second. The saving is about 150 ms per second.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Make `observe` append the sample only. Change `p95` into a property that sorts on demand and returns the value. The one caller at `perfburn/ingest/pipeline.py:69` then pays one sort per batch. For a further cut, replace the sort with a bounded histogram of the sample values. Measure the CPU time per 100,000 events, before the change and after it.

### [P1] notify makes a blocking HTTP call inside a coroutine

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/pipeline.py:52-59`
- **Hot path**: `Pipeline.run` calls `await self.notify(event)` for each event with the destination `alerts` at `perfburn/ingest/pipeline.py:67-68`. The route table sends both `error` and `warn` to `alerts` (`perfburn/config.py:30-31`).
- **Problem**: `urllib.request.urlopen` is a blocking call. The coroutine does not release the event loop during the call. So one webhook call stops all other work in the process: the queue does not drain, and no other batch makes progress. The default `webhook_url` is the empty string (`perfburn/config.py:11`), and the guard at `perfburn/ingest/pipeline.py:54` returns early in that case. So this defect is inactive with the default config and active as soon as an operator sets `webhook_url`.
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
- **Estimate**: This is an estimate. Assumptions: an operator sets `webhook_url`; 10% of the 5,000 events per second carry the level `error` or `warn`, so 500 alerts per second; one webhook call costs 10 ms, which is one 50 µs round trip plus the TCP setup plus the work of the remote handler.
  - 500 alerts per second × 10 ms = 5 s of blocked event loop for each 1 s of wall time. The process stalls.
  - `http_timeout_s` is 5 (`perfburn/config.py:21`). One slow webhook stops all ingest for up to 5 s. At 5,000 events per second, the queue then gains 25,000 events, and `queue_depth` is 10,000 (`perfburn/config.py:12`). So `submit` blocks and the producer stalls too.
  - The code also builds one HTTP connection per event. Reuse of one connection removes one TCP setup per call.
  - After the fix with `asyncio.to_thread`, the event loop keeps running. After the fix with one batched call per batch, the call count falls from 500 per second to 10 per second, a factor of 50.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the call: `await asyncio.to_thread(urllib.request.urlopen, url, data=payload, timeout=timeout)`. Better, collect the alert events of one batch and send one request per batch. Measure the event loop lag and the events per second with `webhook_url` set.

### [P1] build_query_index scans the whole events table and no code reads the result

- **Category**: Redundant Work
- **Location**: `perfburn/app.py:22-28`, `perfburn/app.py:34`, `perfburn/app.py:52`
- **Hot path**: `app.main` calls `build_query_index(conn)` at `perfburn/app.py:34` for every mode, so both `ingest` and `query` pay it. `cli.main` calls `app.main(mode="query", ...)` at `perfburn/cli.py:43`, once per `perfburn query` invocation. The command line runs once per invocation (`perfburn/cli.py:1`), so a frequent invocation makes this startup cost warm.
- **Problem**: The function runs `SELECT id, tags FROM events` with no `WHERE` clause and calls `fetchall()`. It then builds a dict of lists over every tag of every row. `app.main` puts the result in the wiring dict under the key `index`, and no consumer reads that key. `Searcher.__init__` takes only the connection (`perfburn/query/search.py:17-18`). So the whole cost is waste.
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
- **Estimate**: This is an estimate. Assumption: the events table holds 10,000,000 rows. This is a low estimate: `retention_days` is 30 (`perfburn/config.py:15`), and 5,000 events per second gives 432,000,000 rows per day.
  - The two columns hold about 60 bytes per row, so the read is about 600 MB. At 1 ms per 1 MB from SSD, that is about 0.6 s of I/O.
  - The Python object build dominates. Each row costs one tuple, two `str` objects, one `split(",")`, and one or more `setdefault` calls: about 1.5 µs. 10,000,000 × 1.5 µs = about 15 s.
  - `fetchall()` holds all rows at one time. The rows plus the index dict need about 2 GB of resident memory.
  - Every `perfburn query` invocation pays about 15 s and about 2 GB.
  - Every ingest process start pays the same. During that time the process does not read the queue, so the upstream producer backs up at 5,000 events per second.
  - After the fix the cost is zero. The saving is about 15 s and about 2 GB per process start.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete `build_query_index` and delete the `index` key from the return value at `perfburn/app.py:52`. Check first that no code outside this repository reads the key. If a tag index is a real requirement, build it in SQL with an index on the tag column, or use an FTS table. Measure the wall time of `perfburn query` from start to first result.

### [P1] EventStore commits and dispatches one thread hop for each event

- **Category**: I/O and Round Trips
- **Location**: `perfburn/store/db.py:20-29`, `perfburn/ingest/pipeline.py:41`, `perfburn/ingest/pipeline.py:44-50`
- **Hot path**: `Pipeline.process_one` calls `await self._store.insert_event(enriched)` at `perfburn/ingest/pipeline.py:41`. `process_batch` awaits one `process_one` per line in order at `perfburn/ingest/pipeline.py:47`. `run` calls `process_batch` per queue item at `perfburn/ingest/pipeline.py:64`. The rate is about 5,000 events per second (README:10) and the batch size default is 500 (`perfburn/config.py:8`).
- **Problem**: `_insert_sync` runs one `INSERT` and then one `commit()` for each event. `insert_event` wraps each single insert in `asyncio.to_thread`, so each event costs one round trip to a worker thread. `process_batch` awaits each insert in order, so a batch of 500 events runs 500 sequential commits and 500 sequential thread hops. `db.py:12` sets `journal_mode=WAL` but does not set `synchronous`, so the value stays at FULL and each commit fsyncs the WAL. The same file already holds the correct pattern: `flush_metrics` uses `executemany` plus one commit at `perfburn/store/db.py:39-42`.
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
- **Estimate**: This is an estimate. Assumptions: one commit costs one fsync of 1 ms, the low end of the 1 ms to 10 ms range; one `asyncio.to_thread` call costs 70 µs.
  - One commit per event caps throughput at 1,000 events per second. The target is 5,000 events per second (README:10). The gap is a factor of 5.
  - At 10 ms per fsync, the cap falls to 100 events per second, a factor of 50 below the target.
  - Each `asyncio.to_thread` call builds one future, submits to the executor, wakes a worker, and returns through `call_soon_threadsafe`: about 70 µs. At 5,000 events per second that is 350 ms per second of dispatch cost alone, about 35% of one core, with no database work included.
  - A second effect: `Enricher` holds the same connection object, because `app.main` passes `store.connection` at `perfburn/app.py:44`. SQLite serializes access per connection. So each `lookup_source` call at `perfburn/ingest/enrich.py:14` can wait behind an fsync of 1 ms to 10 ms.
  - After the fix, one batch of 500 events costs 1 fsync (1 ms) in place of 500 fsyncs (500 ms), and 1 thread hop (70 µs) in place of 500 hops (35 ms). The write cost per batch falls from about 535 ms to about 1.1 ms, a factor of about 480.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Change `process_batch` to collect the enriched events of one batch. Add a method that takes the list, runs one `executemany`, and commits one time. Wrap that single method in one `asyncio.to_thread` call. Copy the shape of `flush_metrics` at `perfburn/store/db.py:36-43`. Also consider `PRAGMA synchronous=NORMAL`; state the trade-off, because a power loss can then lose the last committed rows. Measure the events per second and the fsync count per 100,000 events.

### [P1] Searcher loads every row of the day and all six columns

- **Category**: Data Layout
- **Location**: `perfburn/query/search.py:20-23`, `perfburn/query/search.py:34-35`
- **Hot path**: `Searcher.search` calls `self.load_rows(day)` at `perfburn/query/search.py:35`, then `match_tags` iterates every row at `perfburn/query/search.py:26`. The documented rate is a few searches per second (README:11). Note: no code in this repository calls `search()`. `app.main` only builds the `Searcher` at `perfburn/app.py:49`, so the caller is outside this repository.
- **Problem**: `load_rows` selects all columns with `SELECT *` and calls `fetchall()`, so it holds every row of the day in memory at one time. It then builds one dict of 6 keys for each row. `search` reads only three fields: `id`, `day`, and `tags` (`perfburn/query/search.py:29` and `perfburn/query/search.py:42`). The `payload` column holds the whole normalized field string that `parser.normalize` produces (`perfburn/store/db.py:25`), so it is the largest column, and no code reads it. The tag filter runs in Python, so SQL cannot drop any row.
- **Evidence**:
```python
    def load_rows(self, day):
        cur = self._conn.execute("SELECT * FROM events WHERE day = ?", (day,))
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
```
- **Estimate**: This is an estimate. Assumption: one day holds 1,000,000 rows. This is a low estimate: 5,000 events per second gives 432,000,000 rows per day.
  - Each row costs one `zip` object plus one dict of 6 keys: about 3 µs. 1,000,000 × 3 µs = about 3 s of CPU per query.
  - The row data adds about 200 bytes per row for the 6 columns, so about 200 MB of resident memory per query. At 1 ms per 1 MB from SSD, the read adds about 200 ms.
  - The rate is a few searches per second (README:11). One query already costs about 3 s, so the service cannot hold that rate. Two concurrent queries need about 400 MB.
  - `search_page_size` is 100 (`perfburn/config.py:19`), so the caller receives 100 rows after the code loads 1,000,000 rows.
  - After the fix: 3 columns in place of 6 cuts the row size by about half. A tag filter in SQL with a 1% match rate cuts the row count from 1,000,000 to 10,000. Tuples in place of dicts cut the per-row build to about 0.3 µs. The CPU per query falls from about 3 s to about 30 ms, a factor of about 100.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Select only `id`, `day`, and `tags`. Push the tag filter into SQL with a `tags LIKE ?` clause per tag, or add an FTS table for the tag column. Keep the rows as tuples and index them by position. Add an index on the tag column. Keep the `idx_events_day` index in use (`perfburn/migrate.py:12`). Measure the wall time and the peak resident memory for one query against a table with one full day of rows.

### [P2] Enricher deep-copies each event

- **Category**: Allocation and Copies
- **Location**: `perfburn/ingest/enrich.py:32-33`
- **Hot path**: `Pipeline.process_one` calls `self._enricher.enrich(event)` at `perfburn/ingest/pipeline.py:39`, once per event, about 5,000 times per second (README:10).
- **Problem**: `copy.deepcopy` walks the object graph, keeps a memo dict, and dispatches per object type. The event holds only strings under `fields` (`perfburn/ingest/pipeline.py:35`). Strings are immutable, so a shallow copy of the `fields` dict gives the same isolation. The code pays the full `deepcopy` machinery for no extra safety.
- **Evidence**:
```python
    def enrich(self, event):
        enriched = copy.deepcopy(event)
```
- **Estimate**: This is an estimate. Assumption: the event holds 2 top-level keys and 5 fields, which matches the test line at `tests/test_pipeline.py:13` with the fields `id`, `level`, `source_id`, `ip`, and `ts`.
  - `deepcopy` visits about 8 objects. Each visit costs a type dispatch, one `id()` call, and one `memo` probe. It also allocates two dicts. The total is about 10 µs.
  - A manual copy `{"id": event["id"], "fields": dict(event["fields"])}` costs about 0.5 µs.
  - At 5,000 events per second: 50 ms per second now, and about 2.5 ms per second after the fix.
  - The saving is about 47 ms per second, about 5% of one core.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the `deepcopy` call with `{"id": event["id"], "fields": dict(event["fields"])}`. Confirm first that no caller mutates a nested value after `enrich` returns. `process_one` uses the return value only (`perfburn/ingest/pipeline.py:39-42`), so the change is safe for the current callers. Measure the CPU time per 100,000 events.

### [P2] lookup_source runs one SQLite query for each event

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/enrich.py:13-19`
- **Hot path**: `Enricher.enrich` calls `self.lookup_source(...)` at `perfburn/ingest/enrich.py:34`. `Pipeline.process_one` calls `enrich` at `perfburn/ingest/pipeline.py:39`, about 5,000 times per second (README:10).
- **Problem**: The method runs one `SELECT` for each event against a small, slow-change table. The `sources` table uses `id TEXT PRIMARY KEY` (`perfburn/migrate.py:9-10`), so the lookup is an index seek and not a scan, but the per-call overhead still repeats 5,000 times per second. `perfburn/config.py:24` holds the key `source_cache_rows` with the value 0, and no code reads that key. So the cache switch exists in the config and not in the code.
- **Evidence**:
```python
    def lookup_source(self, source_id):
        cur = self._conn.execute(
            "SELECT name, tier FROM sources WHERE id = ?", (source_id,))
        row = cur.fetchone()
```
- **Estimate**: This is an estimate. Assumptions: the table pages stay in the SQLite page cache; one `execute` plus one `fetchone` costs 7 µs, which covers the statement cache probe, the btree seek, and the row decode. No network is involved, so the 50 µs round trip does not apply.
  - At 5,000 events per second: 35 ms per second, about 3.5% of one core.
  - A second cost comes from the shared connection. `app.main` passes `store.connection` to the `Enricher` at `perfburn/app.py:44`, and the writer thread commits on that same connection (`perfburn/store/db.py:26`). SQLite serializes access per connection, so each lookup can wait behind one fsync of 1 ms to 10 ms. At 5,000 events per second that wait dominates every other cost on the path. The per-event commit finding above removes most of it.
  - After the fix, one dict lookup costs about 0.05 µs. The saving is about 35 ms per second plus the removal of the wait on the connection.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Load the `sources` table into a dict one time in `Enricher.__init__`. Read `source_cache_rows` to bound the entry count. Add a refresh interval if new source rows must appear without a restart. State the trade-off: a cache hides a new source row until the next refresh. Give the `Enricher` its own read connection so that it does not share the writer connection. Measure the query count per 100,000 events.

### [P2] The debug log call builds a JSON string for every event

- **Category**: Observability Overhead
- **Location**: `perfburn/ingest/pipeline.py:38`
- **Hot path**: `Pipeline.process_one` runs this line for each event that passes the duplicate check, at `perfburn/ingest/pipeline.py:38`, about 5,000 times per second (README:10).
- **Problem**: Python evaluates the argument `json.dumps(event)` before it calls `logger.debug`. So the process pays the serialization cost at every log level, and `logging` then discards the record. No module in this repository calls `logging.basicConfig` or `setLevel`, so the root logger keeps the default level WARNING. The `log_level` value in `perfburn/config.py:14` is never read by any code. So every deployment pays this cost and no deployment emits the output.
- **Evidence**:
```python
        logger.debug("accepted event %s", json.dumps(event))
```
- **Estimate**: This is an estimate. Assumption: the event holds 2 top-level keys and 5 short string fields.
  - `json.dumps` of that structure costs about 5 µs. The discarded `logger.debug` call adds about 1 µs for the level test and the record path.
  - Total about 6 µs per event. At 5,000 events per second: 30 ms per second, about 3% of one core.
  - After the fix with an `isEnabledFor` guard, the cost falls to one attribute lookup plus one integer compare, about 0.1 µs. The saving is about 29 ms per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Guard the call: `if logger.isEnabledFor(logging.DEBUG): logger.debug("accepted event %s", json.dumps(event))`. Also make the code read `log_level` from the config and call `setLevel`, so that the level has an effect. Measure the CPU time per 100,000 events.

### [P2] match_tags splits the same tag string one time for each query tag

- **Category**: Algorithms
- **Location**: `perfburn/query/search.py:25-32`
- **Hot path**: `Searcher.search` calls `self.match_tags(rows, tags)` at `perfburn/query/search.py:36`. The inner loop at `perfburn/query/search.py:27-29` runs once for each row of the day and once for each query tag. The row count per day is large; see the Searcher finding above.
- **Problem**: The code calls `row["tags"].split(",")` inside the loop over `tags`. The result does not depend on `tag`, so a query with 3 tags splits the same string 3 times and allocates 3 lists. The membership test `tag in <list>` is then a linear scan of the list.
- **Evidence**:
```python
        for row in rows:
            for tag in tags:
                if tag in row["tags"].split(","):
                    matched.append(row)
                    break
```
- **Estimate**: This is an estimate. Assumptions: 1,000,000 rows per day; 3 query tags; 5 stored tags per row; one `split(",")` of a short string costs 1 µs.
  - Per row now: up to 3 splits × 1 µs = 3 µs, plus up to 15 string comparisons.
  - Over 1,000,000 rows: about 3 s of CPU per query.
  - After the fix: 1 split (1 µs) plus 3 set probes (0.15 µs) = about 1.15 µs per row, so about 1.15 s per query.
  - The saving is about 1.85 µs per row and about 1.85 s per query.
  - The `break` statement helps a row that matches on the first tag. It does not help a row that matches on the last tag or that matches no tag, and most rows match no tag.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Move the split above the tag loop and build a set: `row_tags = set(row["tags"].split(","))`. Then test `if row_tags & tag_set:` with a set that `search` builds one time from `tags`. Apply the SQL filter from the Searcher finding first, because that change cuts the row count that reaches this loop. Measure the wall time of one query with 3 tags.

### [P2] search sorts every matched row to return 100 rows

- **Category**: Algorithms
- **Location**: `perfburn/query/search.py:43-44`
- **Hot path**: `Searcher.search` builds `scored` in the loop at `perfburn/query/search.py:38-42`, then sorts the whole list at `perfburn/query/search.py:43`. It returns `page_size` rows at `perfburn/query/search.py:44`, and the default is 100 (`perfburn/config.py:19`).
- **Problem**: A full sort orders every matched row, and the caller reads only the first 100. The code needs the top 100 elements and not a full order.
- **Evidence**:
```python
        scored.sort(reverse=True)
        return scored[:page_size]
```
- **Estimate**: This is an estimate. Assumptions: 100,000 matched rows; one comparison of a 4-element tuple costs 60 ns, because the first element decides most cases.
  - A full sort runs about 100,000 × log2(100,000) = 1,700,000 comparisons: about 100 ms.
  - `heapq.nlargest(100, scored)` compares each element against the heap top and pushes rarely: about 100,000 comparisons plus a small number of heap operations, so about 10 ms.
  - The saving is about 90 ms per query, a factor of about 10 on this step.
  - With 10,000 matched rows the saving falls to about 7 ms per query.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the sort and the slice with `heapq.nlargest(page_size, scored)`. The tuple holds `(score, id, day, tags)`, so equal scores fall through to string comparisons; `nlargest` keeps that same order rule. Measure the wall time of one query that matches 100,000 rows.

### [P3] parse_line compiles the pattern and rewrites the line for every event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/parser.py:10-11`, `perfburn/ingest/parser.py:14-15`
- **Hot path**: `Pipeline.process_one` calls `parser.parse_line(line)` at `perfburn/ingest/pipeline.py:34`, once per event, about 5,000 times per second (README:10).
- **Problem**: `parse_line` calls `re.compile(FIELD_PATTERN)` on every call. The pattern is a `str`, so the internal `re` cache returns the compiled object and no recompile happens. The call still costs a function call, a flag check, a hash of the pattern text, and a dict probe. `sanitize` calls `str.translate` with a 32-entry dict, which does one dict lookup per character and allocates a new string.
- **Evidence**:
```python
def sanitize(raw):
    return raw.translate(_CONTROL)


def parse_line(line):
    pattern = re.compile(FIELD_PATTERN)
```
- **Estimate**: This is an estimate. Assumptions: the line holds 70 characters, which matches the test line at `tests/test_pipeline.py:13`; one dict lookup inside `str.translate` costs 40 ns.
  - `re.compile` on a cached pattern costs about 0.5 µs.
  - `str.translate` costs about 70 × 40 ns = 2.8 µs, plus one new string of 70 characters.
  - Total about 3.3 µs per event. At 5,000 events per second: 16.5 ms per second, about 1.7% of one core.
  - After the module-level hoist of the compiled pattern, the saving is about 0.5 µs per event, so about 2.5 ms per second.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Hoist the compiled pattern to module level: `_FIELD_RE = re.compile(FIELD_PATTERN)`. Use `_FIELD_RE.finditer(...)` in `parse_line`. This change is safe. Do not remove the `sanitize` call without a test first: `\S` in the pattern matches control characters, so the translate step changes the parsed result. Measure the CPU time of `parse_line` over 100,000 lines.

### [P3] route rebuilds the route map for every event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/pipeline.py:26-31`
- **Hot path**: `Pipeline.process_one` calls `self.route(enriched)` at `perfburn/ingest/pipeline.py:40`, once per event, about 5,000 times per second (README:10).
- **Problem**: `route` builds a new `routes` dict from `self._config.routes` on every call. The list holds 4 entries (`perfburn/config.py:29-34`), and `Config.load` never changes it after the process starts (`perfburn/config.py:59`). So the map is loop-invariant.
- **Evidence**:
```python
    def route(self, event):
        routes = {}
        for entry in self._config.routes:
            routes[entry["match"]] = entry["destination"]
```
- **Estimate**: This is an estimate. Assumption: 4 route entries.
  - Per call: one dict allocation about 0.1 µs, plus 4 passes with two dict reads and one dict store each, about 0.15 µs per pass. Total about 0.7 µs.
  - At 5,000 events per second: 3.5 ms per second, about 0.35% of one core.
  - After the fix the per-event cost is one dict lookup, about 0.05 µs. The saving is about 3.4 ms per second.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the map one time in `Pipeline.__init__` and store it on the instance. Keep `route` limited to the level lookup and the map lookup. Measure the CPU time per 100,000 events.

### [P3] The score_term cache key holds field_length, so the hit rate stays low

- **Category**: Redundant Work
- **Location**: `perfburn/query/search.py:8-13`, `perfburn/query/search.py:41`
- **Hot path**: `Searcher.search` calls `score_term(tag, len(row["tags"]))` in the loop at `perfburn/query/search.py:39-41`, once per matched row and once per query tag.
- **Problem**: The `lru_cache` key is the tuple `(term, field_length)`. `field_length` is `len(row["tags"])` and it changes from row to row, so most calls miss the cache. `maxsize` is 4096, and the key space is the term count times the distinct tag-string lengths, so entries evict often. The cached body is cheap, so the cache overhead can cost more than the computation. The division by `(1.0 + field_length)` is the only part that depends on the row.
- **Evidence**:
```python
@lru_cache(maxsize=4096)
def score_term(term, field_length):
    weight = 1.0
    for ch in term:
        weight += TERM_WEIGHTS.get(ch, 0.1)
    return weight / (1.0 + field_length)
```
- **Estimate**: This is an estimate. Assumptions: a 5-character tag; 3 query tags.
  - The body costs 5 dict lookups plus 5 float additions plus one division: about 0.5 µs.
  - The `lru_cache` wrapper costs a tuple build, a hash, and a dict probe: about 0.35 µs on a hit, and more on a miss because it also inserts and evicts.
  - Per call now: about 0.85 µs. After the fix with the term as the only key, the hit rate rises to near 100% for a small tag set, and the per-call cost falls to about 0.35 µs plus one division.
  - With 10,000 matched rows and 3 tags: 30,000 calls. 25.5 ms now, about 12 ms after the fix. The saving is about 13 ms per query.
  - With 100,000 matched rows the saving grows to about 130 ms per query. The Searcher fix above cuts the matched row count, so it also cuts this saving.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change the cached function so that it takes only `term` and returns the weight sum. Divide by `(1.0 + field_length)` at the call site. Measure the cache hit rate with `score_term.cache_info()` before the change and after it.

## Cold-Code Notes

- `perfburn/query/reportgen.py:51-54` — `render` calls `summarize(conn, day)` 4 times, so it runs 4 identical `GROUP BY` queries over one day in place of 1. Runs once per day via cron (README:12). One call and one reuse of the result removes 3 of the 4 scans.
- `perfburn/store/archive.py:8-9` — `compress_day` reads the whole file into memory with `f.read()` before the gzip write. A file of 1 GB needs 1 GB of resident memory and about 1 s of read time. `shutil.copyfileobj` streams it. Runs once per hour (README:13).
- `perfburn/migrate.py:34-37` — the migration loop commits after each legacy row, so it pays one fsync of 1 ms to 10 ms per row. 1,000,000 legacy rows cost 1,000 s to 10,000 s. One `executemany` plus one commit fixes it. One-shot script (README:14).
- `perfburn/config.py:55-58` — `load` compiles 20 `MULTILINE` patterns and searches the whole text 20 times. One line-by-line parse pass replaces it. Runs once per process start.
- `perfburn/cli.py:16-19` — `resolve_alias` builds a new `[command] + aliases` list per command and calls `name.lower()` per candidate, so it repeats the same lowercase conversion up to 16 times. A module-level dict from alias to command replaces the loop. Runs once per invocation.
- `perfburn/app.py:33` and `perfburn/store/db.py:11` — ingest mode opens two SQLite connections to the same file, one in `app.main` and one in `EventStore.__init__`. The first connection exists only for `build_query_index`, which the P1 finding above removes.
- `perfburn/store/archive.py:19-21` — `prune` sorts the whole directory listing to drop the oldest entries. `retention_days` is 30 (`perfburn/config.py:15`), so the entry count is small. Runs once per hour.
- `tests/test_pipeline.py:10-16` — `make_events` builds a 10,000-line blob with `+=` in a loop. CPython applies the in-place concatenation optimization here, so the growth is near linear and the suite stays fast. A `"".join` over a list makes the intent clear.
- `perfburn/metrics.py:19-20` — `snapshot` has no caller, and no code reads `count` or `window`. Delete them, or add the caller that reads them.

## Not Found

All six categories produced at least one finding, so no category is empty. These are the candidates that I checked and dismissed, with the number that dismissed each one:

- **String concatenation in a loop** — `perfburn/ingest/parser.py:23-27`. `normalize` builds the canonical string with `canonical += ...`. This runs once per event from `perfburn/store/db.py:25`, so the path is hot. It is not quadratic in practice: CPython applies the in-place concatenation optimization when the reference count of the target string is 1, and that condition holds here. With 5 to 8 fields the string reaches about 120 characters, so the total copy cost stays under 500 bytes and under 1 µs. A `"".join` saves about 1 µs of the about 3 µs. The `sorted(fields)` call on 5 to 8 keys costs about 0.6 µs and the canonical order is a requirement (`tests/test_pipeline.py:25-28`). Not worth the change.
- **Metric writes on the hot path** — `perfburn/store/db.py:31-43`. `queue_metric` buffers rows and flushes at 200 rows (`perfburn/config.py:18`), and `flush_metrics` uses `executemany` plus one commit. `Pipeline.run` calls `queue_metric` once per batch (`perfburn/ingest/pipeline.py:69`), and the batch size is 500 (`perfburn/config.py:8`). So one commit covers 100,000 events. This is the correct pattern, and the event insert path should copy it.
- **Map keyed by small dense integers** — `perfburn/ingest/parser.py:7`. `_CONTROL` is a dict keyed by the integers 0 to 31. An array does not replace it, because `str.translate` needs a mapping object. The dict is module-level, so the code builds it one time.
- **Nested maps that cost several lookups per access** — no case found. The `summary` dict at `perfburn/query/reportgen.py:8` holds two flat maps and one list, and each access costs one lookup.
- **Full sort for the top k in the report** — `perfburn/query/reportgen.py:19-21`. `top_events` sorts all rows to return 10. The row count equals the number of distinct destination and region pairs, which the 4 route destinations and a region table of at most 256 entries bound to about 1,000 rows. A sort of 1,000 rows costs about 1 ms, and the function runs once per day. Not worth the change.
- **Missing cache for the duplicate check** — `perfburn/ingest/dedupe.py:5`. The `capacity` bound is deliberate: it caps memory, and the test at `tests/test_pipeline.py:41` fixes the first-in, first-out eviction order. The defect is the list data structure and not the bound. The fix must keep the bound and the order.
- **Sequential awaits on independent operations** — `perfburn/ingest/pipeline.py:44-50`. `process_batch` awaits each event in order. Concurrent awaits do not help here, because all writes go to one SQLite connection and SQLite serializes them. The correct fix is one batched write per batch, which the P1 finding above describes. `ingest_workers` is 1 (`perfburn/config.py:22`) and no code reads that key.