I have what I need. Every number below is a calculated estimate from the documented latency table plus known CPython costs — the sandbox blocked execution, so I did not measure at runtime, and I mark the estimates as such.

# Performance Audit Report

## Summary

`perfburn` parses telemetry log lines, removes duplicates, adds source and region data, and writes rows to SQLite. The hot path is `Pipeline.run` at `perfburn/ingest/pipeline.py:61`, which the README rates at 5,000 events per second. My estimate puts the current cost at about 2,190 µs of CPU per event, against a budget of 200 µs per event on one core. Two defects hold about 93% of that cost. The best quick win is the duplicate check at `perfburn/ingest/dedupe.py:10`: it scans a list of 50,000 strings for each event, and a dict makes the same test O(1).

The estimated per-event cost divides as follows:

| Work | Location | Estimate per event |
|---|---|---|
| List scan for the duplicate check | `dedupe.py:10` | 1,500 µs |
| `pop(0)` memory move of 400 KB | `dedupe.py:14` | 25 µs |
| The code re-reads the region CSV text | `enrich.py:23` | 500 µs |
| `asyncio.to_thread` dispatch | `db.py:29` | 60 µs |
| `sorted()` over a 1,024-sample window | `metrics.py:15` | 60 µs |
| `copy.deepcopy` of the event | `enrich.py:33` | 15 µs |
| SQLite point query for the source | `enrich.py:14` | 10 µs |
| `parse_line` translate and regex scan | `parser.py:15` | 8 µs |
| `json.dumps` for a disabled debug log | `pipeline.py:38` | 6 µs |
| `normalize` sort and string joins | `parser.py:23` | 3 µs |
| The code rebuilds the routes dict | `pipeline.py:27` | 0.8 µs |
| **Total** | | **about 2,190 µs** |

At 2,190 µs per event, one core carries about 460 events per second. That is about 11 times short of the 5,000 events per second target. Findings 1 and 2 remove about 2,025 µs and bring the path to about 163 µs per event, or about 6,100 events per second.

## Findings

### [P1] The duplicate check scans a list of 50,000 strings for each event

- **Category**: Algorithms
- **Location**: `perfburn/ingest/dedupe.py:10-15`
- **Hot path**: `Pipeline.process_one` calls `self._deduper.seen(...)` at `perfburn/ingest/pipeline.py:36`. `process_batch` calls `process_one` for each line at `perfburn/ingest/pipeline.py:45-49`. `run` calls `process_batch` for each queue item at `perfburn/ingest/pipeline.py:64`.
- **Problem**: `Deduper` holds the seen ids in a Python list. The `in` operator on a list is a linear scan. Most events are new, so the test does not exit early and reads all 50,000 entries. `config.py:17` sets `dedupe_capacity` to 50000, so the scan length equals the capacity. The waste grows when an operator raises that value. Line 14 adds a second cost: `pop(0)` moves every remaining pointer down one slot.
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
- **Estimate**: This is an estimate. I assume 30 ns for one string comparison in a CPython list scan, and a full 50,000-entry scan for a new event. The scan costs 50,000 × 30 ns = 1,500 µs per event. The `pop(0)` call moves 50,000 pointers, or 400 KB. Memory reads run at 64 µs per MB, so the move costs about 25 µs. The total is about 1,525 µs per event, or 70% of the 2,190 µs path. At 5,000 events per second the pipeline needs 7.6 seconds of CPU per second of events for this check alone. A dict lookup costs about 0.05 µs, so the fix removes about 1,525 µs per event.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the list with a dict and a `collections.deque` of keys. Test membership against the dict. Append each new key to the deque. Evict the oldest key with `deque.popleft()` when the size passes the capacity. Keep the first-in-first-out eviction order, because `tests/test_pipeline.py:41-46` asserts that `"a"` is evicted after `"c"` at capacity 2. Measure events per second at the queue drain in `pipeline.py:64` before the change and after it.

### [P1] The enricher re-reads the region CSV text for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/enrich.py:21-30`
- **Hot path**: `Enricher.enrich` calls `self.tag_region(event)` at `perfburn/ingest/enrich.py:35`. `Pipeline.process_one` calls `self._enricher.enrich(event)` at `perfburn/ingest/pipeline.py:39`, once for each line in each batch.
- **Problem**: `tag_region` builds the `regions` dict on every call. It wraps the stored CSV text in a `StringIO`, runs `csv.reader` over every row, and calls `.strip()` twice for each row. The text never changes after startup, because `app.read_region_csv` reads the file once at `perfburn/app.py:14-19` and passes it to the constructor at `perfburn/app.py:44`. The function then uses one key from the dict that it just built.
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
- **Estimate**: This is an estimate. The code keys the table by the first octet of an IP address at line 29, so the file holds at most 256 rows. I assume 200 rows. One row costs about 2.5 µs for the reader step, the length test, the unpack, two `strip()` calls, and the dict store. 200 rows cost about 500 µs per event. At 5,000 events per second that is 2.5 seconds of CPU per second of events, or 23% of the 2,190 µs path. After the fix the call costs one dict `get` plus one `split`, about 0.35 µs. The saving is about 500 µs per event. Rescale the figure at 2.5 µs per CSV row if the real file has a different row count.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the `regions` dict once in `Enricher.__init__` and store it on the instance. Keep `tag_region` as one dict lookup. Measure the CPU time of `enrich` before the change and after it.

### [P1] The store commits and dispatches a thread for each event

- **Category**: I/O and Round Trips
- **Location**: `perfburn/store/db.py:20-29`
- **Hot path**: `Pipeline.process_one` awaits `self._store.insert_event(enriched)` at `perfburn/ingest/pipeline.py:41`. `process_batch` awaits `process_one` for each line in turn at `perfburn/ingest/pipeline.py:45-49`, so the batch pays the cost 500 times in series. `config.py:8` sets `batch_size` to 500.
- **Problem**: `_insert_sync` calls `self._conn.commit()` after every single-row insert. One commit in WAL mode is one fsync to disk. `insert_event` also wraps each row in a separate `asyncio.to_thread` call, which copies the context and hands the work to the thread pool once per row. Two extra effects make this worse. First, `process_batch` awaits each row in turn, so the inserts never overlap. Second, `Enricher` holds the same connection object, which `app.py:44` passes from `store.connection`, and `db.py:11` opens it with `check_same_thread=False`. The per-event `lookup_source` query at `enrich.py:14` runs on the event loop thread and contends with the commits on the worker thread through the SQLite connection mutex.
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
- **Estimate**: This is an estimate. One commit or fsync costs 1 to 10 ms; I assume 2 ms for a WAL commit on an SSD. At 5,000 events per second the store needs 5,000 × 2 ms = 10 seconds of fsync per second of events. The fsync alone caps throughput at about 500 events per second. The thread dispatch adds about 60 µs per event, or 300 ms per second. One batch of 500 rows costs about 500 × (2 ms + 60 µs) = 1.03 s, and the target rate needs 10 batches per second. After the fix each batch runs one `executemany` and one commit inside one `to_thread` call. That step drops the fsync count from 5,000 per second to 10 per second, a factor of 500. It also drops the thread dispatch cost from 300 ms per second to 0.6 ms per second.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a bulk method to `EventStore` that takes a list of events. Run one `executemany` and one commit for the whole list. Call it once for each batch from `process_batch`. Give `Enricher` its own read connection to remove the mutex contention. Measure the fsync count with `strace` or the commit count, and measure batches per second at `pipeline.py:64`.

### [P1] The startup builds a full-table index that no caller reads

- **Category**: Redundant Work
- **Location**: `perfburn/app.py:22-28`, `perfburn/app.py:34`, `perfburn/app.py:52`
- **Hot path**: `app.main` calls `build_query_index(conn)` at `perfburn/app.py:34`. That line runs before the mode test at `perfburn/app.py:40`, so both `perfburn ingest` and `perfburn query` pay the cost. `cli.main` calls `app.main` at `perfburn/cli.py:42` and `perfburn/cli.py:44`.
- **Problem**: `build_query_index` runs `SELECT id, tags FROM events` with no `WHERE` clause and no `LIMIT`, then calls `fetchall()`. It materializes every row of the events table in memory and builds a dict of tag lists. No code reads the result. `main` returns it under the `index` key at `perfburn/app.py:52`, and no caller reads that key. `Searcher` queries SQL directly at `perfburn/query/search.py:21`. The work is dead, and every process start pays it. This includes every deploy and every restart after a crash.
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
- **Estimate**: This is an estimate. At 5,000 events per second one day holds 5,000 × 86,400 = 432,000,000 rows. `config.py:15` sets `retention_days` to 30, so the table holds about 13 billion rows. `fetchall()` cannot hold that set in memory, so the start fails. Take a much smaller table of 30 million rows as a lower bound. The loop then costs 30,000,000 × 1.2 µs = 36 seconds, and the dict holds more than 30 million entries. The fix removes 100% of this cost.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete the `build_query_index` function, the call at `perfburn/app.py:34`, and the `index` key at `perfburn/app.py:52`. Measure the wall clock time of `app.main` before the change and after it. Add the index back only as a SQL index if a caller needs tag lookups. See the tags column finding below.

### [P1] Tag search loads a whole day of rows and filters them in Python

- **Category**: Algorithms
- **Location**: `perfburn/query/search.py:20-44`
- **Hot path**: `Searcher.search` calls `self.load_rows(day)` at `perfburn/query/search.py:35`, then `self.match_tags(rows, tags)` at `perfburn/query/search.py:36`. The README rates this path at a few searches per second. `app.main` builds the `Searcher` at `perfburn/app.py:49`.
- **Problem**: `load_rows` runs `SELECT * FROM events WHERE day = ?` and calls `fetchall()`. The only index on the table is `idx_events_day` at `perfburn/migrate.py:12`, so the day filter uses an index, but the query still returns every row for that day. `SELECT *` pulls all six columns. This includes `payload`, the largest column, which `db.py:25` fills with the output of `parser.normalize`. The search never reads `payload`; it reads only `id`, `day`, and `tags`. Line 23 then builds one Python dict for each row. `match_tags` applies the tag filter in Python, and it calls `row["tags"].split(",")` once for each tag inside the row loop at line 29. Line 43 sorts every matched row to return only `page_size` rows, which `config.py:19` sets to 100.
- **Evidence**:
```python
def load_rows(self, day):
    cur = self._conn.execute("SELECT * FROM events WHERE day = ?", (day,))
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]

def match_tags(self, rows, tags):
    for row in rows:
        for tag in tags:
            if tag in row["tags"].split(","):
```
- **Estimate**: This is an estimate. At 5,000 events per second one day holds 432,000,000 rows. I assume 250 bytes for a row with the `payload` column. The query then reads 432M × 250 B = 108 GB. Sequential SSD reads run at 1 ms per MB, so the read costs about 108 seconds for each search. Line 23 builds 432 million dicts at about 1.5 µs each, or about 648 seconds. The dicts need about 432M × 350 B = 150 GB of memory, so the process raises `MemoryError` first. The path serves a few searches per second, so it is more than 100 times short even at a much smaller row count. Column pruning alone removes the `payload` bytes. A SQL tag filter with a `LIMIT` clause cuts the returned rows from 432 million to about 100.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Apply four changes. Select only `id`, `day`, and `tags` instead of `*`. Push the tag filter into the SQL `WHERE` clause. Add a `LIMIT` clause for `page_size`. Replace the full sort at line 43 with `heapq.nlargest(page_size, scored)`. Split each row's tags once into a set, then intersect that set with the query tags. Measure the rows returned per search and the wall clock time for one search. Read the tags column finding below for the indexed form of this fix.

### [P1] A blocking HTTP call runs inside the async pipeline loop

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/pipeline.py:52-59`
- **Hot path**: `run` awaits `self.notify(event)` at `perfburn/ingest/pipeline.py:68`, inside the `for event in batch` loop at `perfburn/ingest/pipeline.py:65`. `run` is the single consumer coroutine, and `config.py:22` sets `ingest_workers` to 1. `route` sends every event with level `error` or `warn` to the `alerts` destination, because `config.py:30-31` maps both levels to `alerts`.
- **Problem**: `notify` is an `async def` function, but it calls `urllib.request.urlopen`, which is a synchronous call. The call blocks the event loop thread for the whole round trip. No other coroutine runs during that time, so the queue drain stops. `config.py:21` sets `http_timeout_s` to 5, so one slow endpoint blocks the loop for 5 seconds.
- **Evidence**:
```python
async def notify(self, event):
    url = self._config.get("webhook_url")
    if not url:
        return
    ...
    urllib.request.urlopen(url, data=payload, timeout=timeout)
```
- **Estimate**: This is an estimate. `config.py:11` sets `webhook_url` to an empty string, so line 55 returns early and the cost is zero under the default config. This finding applies when an operator sets the URL. I assume that 5% of events carry level `error` or `warn`, which gives 250 alerts per second at 5,000 events per second. One HTTP round trip in the same datacenter plus server time costs 1 to 10 ms; I assume 5 ms. The loop then needs 250 × 5 ms = 1,250 ms of stall per second of events, and one second holds only 1,000 ms. The queue grows without limit. A single 5-second timeout blocks about 25,000 events at 5,000 events per second. `config.py:12` caps `queue_depth` at 10000 batches, so `submit` at `pipeline.py:24` blocks the producer after that.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Move the HTTP call off the event loop thread. Wrap the call in `asyncio.to_thread`, which the codebase already uses at `db.py:29`. Better, collect the alert events for the batch and post them in one request. Cap the concurrent requests with a semaphore. Measure the queue depth at `pipeline.py:63` and the batch drain rate with the webhook set.

### [P2] The tags column holds a comma list, so tag search cannot use an index

- **Category**: Data Layout
- **Location**: `perfburn/migrate.py:8`, `perfburn/migrate.py:12`, `perfburn/store/db.py:25`, `perfburn/query/search.py:29`
- **Hot path**: `Searcher.match_tags` splits the string for each row at `perfburn/query/search.py:29`, under the row loop at `perfburn/query/search.py:26`. `search` calls `match_tags` for each user query at `perfburn/query/search.py:36`.
- **Problem**: `db.py:25` writes the tags as one comma-joined string. The schema at `migrate.py:8` declares `tags TEXT`, and the only index is `idx_events_day` on the `day` column at `migrate.py:12`. SQLite cannot index the parts inside the string, so no tag query can seek. Any tag search must read every row for the day and split the string in Python. This layout is the root cause of the previous finding. A `LIMIT` clause helps the previous finding, but it changes which rows the search returns, because the filter no longer ranks all matches.
- **Evidence**:
```python
" id TEXT, day TEXT, destination TEXT, region TEXT, tags TEXT, payload TEXT)",
"CREATE INDEX IF NOT EXISTS idx_events_day ON events (day)",
```
```python
if tag in row["tags"].split(","):
```
- **Estimate**: This is an estimate. One day holds 432,000,000 rows at 5,000 events per second. The current layout forces a scan of all 432 million rows for each tag search. An index seek on a normalized tag table reads about 100 matched rows plus the index pages. That is a reduction of about 4,000,000 times in rows read. The write path pays a cost in exchange: each event inserts one row for each of its tags. I assume 3 tags per event, so the insert count rises from 5,000 to 20,000 rows per second. Combine this change with the batch commit fix above, or the write path becomes the new limit.
- **Impact**: High
- **Effort**: Large (days)
- **Suggested fix**: Profile the query path first, because this change touches the schema, the write path, and the migration. Add an `event_tags` table with the columns `event_id` and `tag`, and add an index on `(tag, event_id)`. Write one row for each tag in the batch insert. Join against that table in `search`. Keep the old column until the migration completes. Measure the rows read per search with `EXPLAIN QUERY PLAN`.

### [P2] The latency tracker sorts the whole sample window for each event

- **Category**: Observability Overhead
- **Location**: `perfburn/metrics.py:12-17`
- **Hot path**: `run` calls `self._tracker.observe(len(event["fields"]))` at `perfburn/ingest/pipeline.py:66`, inside the `for event in batch` loop at `perfburn/ingest/pipeline.py:65`.
- **Problem**: `observe` calls `sorted(self._samples)` on every event and computes the 95th percentile. The consumer reads the result only once for each batch, at `perfburn/ingest/pipeline.py:69`. The pipeline therefore discards 499 of every 500 computed values, because `config.py:8` sets `batch_size` to 500. `config.py:16` sets `sample_window` to 1024, so each sort orders 1,024 samples.
- **Evidence**:
```python
def observe(self, value):
    self._samples.append(value)
    self.count += 1
    ordered = sorted(self._samples)
    index = int(len(ordered) * 0.95)
    self.p95 = ordered[min(index, len(ordered) - 1)]
```
- **Estimate**: This is an estimate. A sort of 1,024 integers runs 1,024 × log2(1024) = 10,240 comparisons. I assume about 60 µs for the sort plus the copy of the deque into a list. At 5,000 events per second that is 300 ms of CPU per second of events. The value changes once per batch, so 10 sorts per second suffice at 10 batches per second. That drops the cost to 0.6 ms per second, a saving of about 299 ms per second. The saving equals about 37% of the 163 µs path that remains after the two top fixes.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Make `observe` append the sample only. Turn `p95` into a property that sorts on read. The reader at `pipeline.py:69` then triggers one sort for each batch, and it sees the same window contents as before. Measure the CPU time of `observe` before the change and after it.

### [P2] The enricher deep-copies every event

- **Category**: Allocation and Copies
- **Location**: `perfburn/ingest/enrich.py:33`
- **Hot path**: `Pipeline.process_one` calls `self._enricher.enrich(event)` at `perfburn/ingest/pipeline.py:39`, once for each line in each batch of 500.
- **Problem**: `enrich` calls `copy.deepcopy(event)`. `copy.deepcopy` keeps a memo dict, dispatches on the type of each value, and recurses into the nested `fields` dict. The event holds only strings, and no code mutates `event["fields"]` after the call. `process_one` sets one top-level key at `perfburn/ingest/pipeline.py:40`. `route` reads the fields at `perfburn/ingest/pipeline.py:30`. `_insert_sync` reads the fields at `perfburn/store/db.py:25`. A shallow copy of the top-level dict therefore gives the same protection.
- **Evidence**:
```python
def enrich(self, event):
    enriched = copy.deepcopy(event)
```
- **Estimate**: This is an estimate. A `copy.deepcopy` call on a two-level dict with 7 string values costs about 15 µs. At 5,000 events per second that is 75 ms of CPU per second of events. `dict(event)` costs about 0.5 µs, so the saving is about 14.5 µs per event, or 72 ms per second. The saving equals about 9% of the 163 µs path that remains after the two top fixes.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace `copy.deepcopy(event)` with `dict(event)`. Confirm that no code mutates `enriched["fields"]` after the call. Measure the CPU time of `enrich` before the change and after it.

### [P2] The source lookup runs one SQLite query for each event and holds no cache

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/enrich.py:13-19`
- **Hot path**: `Enricher.enrich` calls `self.lookup_source(...)` at `perfburn/ingest/enrich.py:34`. `Pipeline.process_one` calls `enrich` at `perfburn/ingest/pipeline.py:39`, once for each line in each batch.
- **Problem**: `lookup_source` runs a `SELECT` against the `sources` table for every event. The query seeks on a primary key, which `migrate.py:10` declares, so each query is cheap. The count is the problem, not the cost of one query. The source set is small and it changes rarely. `config.py:24` defines `source_cache_rows` with the value `0`, so a cache was planned and stays off. The query also runs on the event loop thread and shares one connection with the insert worker, as the batch commit finding above describes.
- **Evidence**:
```python
def lookup_source(self, source_id):
    cur = self._conn.execute(
        "SELECT name, tier FROM sources WHERE id = ?", (source_id,))
    row = cur.fetchone()
```
- **Estimate**: This is an estimate. An in-process SQLite primary key seek with a warm page cache costs about 10 µs, and this includes the Python `sqlite3` layer, the cursor object, and the `fetchone` call. At 5,000 events per second that is 50 ms of CPU per second of events. A dict cache lookup costs about 0.1 µs. The saving is about 10 µs per event, or 50 ms per second, once the cache holds the hot sources. `tests/test_pipeline.py:13` generates 5 distinct source ids, which suggests a small set and a high hit rate.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a bounded dict cache keyed by `source_id`, and honor the existing `source_cache_rows` setting. Note the trade-off: a cache delays any edit to the `sources` table. Add a time-to-live value, or accept the delay until the next restart. Measure the query count against the `sources` table before the change and after it.

### [P2] A disabled debug log still serializes every event to JSON

- **Category**: Observability Overhead
- **Location**: `perfburn/ingest/pipeline.py:38`
- **Hot path**: `Pipeline.process_one` runs this line for every accepted event. `process_batch` calls `process_one` for each line at `perfburn/ingest/pipeline.py:45-49`.
- **Problem**: The `%s` placeholder defers the string format, but Python evaluates `json.dumps(event)` at the call site before it calls `logger.debug`. The process therefore serializes every event even when the debug level is off. The level is always off in the current code. No module calls `logging.basicConfig` or `setLevel` anywhere in the package, so the root logger keeps its default level of `WARNING`. `config.py:13` defines `log_level` with the value `INFO`, and no code reads that key.
- **Evidence**:
```python
logger.debug("accepted event %s", json.dumps(event))
```
- **Estimate**: This is an estimate. A `json.dumps` call on this event dict costs about 6 µs. At 5,000 events per second that is 30 ms of CPU per second of events. The fix removes all of that cost while the debug level stays off. The saving equals about 4% of the 163 µs path that remains after the two top fixes.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Guard the call with `if logger.isEnabledFor(logging.DEBUG):`. Do not put the guard inside the loop body more than once. Separately, apply the `log_level` setting at startup, or delete the unused key at `config.py:13`. Measure the CPU time of `process_one` before the change and after it.

### [P3] The router rebuilds the routes dict for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/pipeline.py:26-31`
- **Hot path**: `Pipeline.process_one` calls `self.route(enriched)` at `perfburn/ingest/pipeline.py:40`, once for each line in each batch of 500.
- **Problem**: `route` loops over `self._config.routes` and builds a 4-entry dict on every call. The route list never changes after startup. `config.load` builds it once at `perfburn/config.py:59`, and it copies the constant `DEFAULT_ROUTES` from `perfburn/config.py:29-34`.
- **Evidence**:
```python
def route(self, event):
    routes = {}
    for entry in self._config.routes:
        routes[entry["match"]] = entry["destination"]
```
- **Estimate**: This is an estimate. Four loop passes, each with a list index, two dict reads, and one dict store, cost about 0.8 µs in total. At 5,000 events per second that is 4 ms of CPU per second of events. The fix removes almost all of that, and it leaves one dict `get` of about 0.05 µs. The saving is about 0.5% of the 163 µs path that remains after the two top fixes.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the routes dict once in `Pipeline.__init__` and store it on the instance. Keep `route` as one dict lookup.

### [P3] parse_line calls re.compile on every event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/parser.py:15`
- **Hot path**: `Pipeline.process_one` calls `parser.parse_line(line)` at `perfburn/ingest/pipeline.py:34`, once for each line in each batch of 500.
- **Problem**: `parse_line` calls `re.compile(FIELD_PATTERN)` on every call. The pattern is a module constant at `perfburn/ingest/parser.py:5`, so the compiled object could live at module level. The cost is smaller than the code suggests, because CPython keeps an internal cache of compiled patterns. The call therefore pays a tuple hash, a dict lookup, and two function calls, not a full compile.
- **Evidence**:
```python
def parse_line(line):
    pattern = re.compile(FIELD_PATTERN)
```
- **Estimate**: This is an estimate. The internal cache lookup costs about 0.5 to 1 µs. A full compile would cost more than 100 µs, but the cache prevents that on all calls after the first. At 5,000 events per second the waste is 2.5 to 5 ms of CPU per second of events. A module-level reference costs about 0.02 µs. The saving is about 0.5% of the 163 µs path that remains after the two top fixes.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Compile the pattern once at module level next to `FIELD_PATTERN`. Reference the compiled object inside `parse_line`.

### [P3] The score cache key holds a variable field length, so the cache mostly misses

- **Category**: Redundant Work
- **Location**: `perfburn/query/search.py:8-13`, `perfburn/query/search.py:41`
- **Hot path**: `Searcher.search` calls `score_term(tag, len(row["tags"]))` at `perfburn/query/search.py:41`, inside the tag loop at `perfburn/query/search.py:40` and the matched-row loop at `perfburn/query/search.py:38`.
- **Problem**: The `lru_cache` key holds both `term` and `field_length`. `field_length` is `len(row["tags"])`, which varies from row to row. The cache therefore stores a separate entry for each pair, and the hit rate stays low. The cache holds 4,096 entries and evicts them under load. The body of the function is cheap: it loops over the characters of one tag and divides once. The cache wrapper adds a tuple build, a hash, and a dict lookup, so the cache gives little or no gain.
- **Evidence**:
```python
@lru_cache(maxsize=4096)
def score_term(term, field_length):
    weight = 1.0
    for ch in term:
        weight += TERM_WEIGHTS.get(ch, 0.1)
    return weight / (1.0 + field_length)
```
- **Estimate**: This is an estimate. The body costs about 0.4 µs for a tag of 3 to 10 characters. The `lru_cache` wrapper costs about 0.15 to 0.25 µs on a hit and more on a miss. The cache is therefore close to break-even at the current hit rate. The `weight` sum depends only on `term`, so a key of `term` alone raises the hit rate to near 100% for a small tag set. The saving is about 0.3 µs for each row and tag pair. This matters only after the search path stops the scan of a whole day; see the P1 search finding.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Cache a function of `term` alone that returns the `weight` sum. Divide by `1.0 + field_length` at the call site. Measure the cache hit rate with `score_term.cache_info()`.

### [P3] compress_day reads the whole file into memory

- **Category**: Allocation and Copies
- **Location**: `perfburn/store/archive.py:7-15`
- **Hot path**: The scheduler calls `compress_day` once per hour, as the README states at line 13. No call site exists inside the package, so the scheduler lives outside this repository.
- **Problem**: `compress_day` calls `f.read()` and holds the whole file in one bytes object. It then writes that object to the gzip stream. The memory need grows with the file size and has no bound.
- **Evidence**:
```python
with open(path, "rb") as f:
    data = f.read()
```
- **Estimate**: This is an estimate. At 5,000 events per second and about 250 bytes for a row, one hour produces about 4.5 GB. The `read()` call then needs 4.5 GB of resident memory. Sequential SSD reads run at 1 ms per MB, so the read costs about 4.5 seconds either way. The gzip CPU cost dominates the total, so the time saving is small. The memory saving is the win: a stream copy with a 64 KB buffer holds a fixed 64 KB instead of 4.5 GB. The function runs 24 times per day, so the time cost is not on a hot path.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the read and write pair with `shutil.copyfileobj(f, out)`. Measure the peak resident memory of the archive step before the change and after it.

### [P4] The tracker keeps a count that no caller reads

- **Category**: Observability Overhead
- **Location**: `perfburn/metrics.py:10`, `perfburn/metrics.py:14`, `perfburn/metrics.py:19-20`
- **Hot path**: `observe` runs `self.count += 1` at `perfburn/metrics.py:14`, once for each event from `perfburn/ingest/pipeline.py:66`.
- **Problem**: No code reads `self.count`, and no code calls `snapshot()`. The pipeline reads only `self._tracker.p95`, at `perfburn/ingest/pipeline.py:69`. The counter increment is therefore dead work on a per-event path.
- **Evidence**:
```python
def snapshot(self):
    return {"p95": self.p95, "count": self.count, "window": len(self._samples)}
```
- **Estimate**: This is an estimate. An attribute read, an integer add, and an attribute store cost about 0.05 µs. At 5,000 events per second that is 0.25 ms of CPU per second of events. The saving is about 0.03% of the 163 µs path that remains after the two top fixes.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: I do not recommend this change now. The saving of 0.25 ms per second does not repay the work, and `snapshot()` helps a future debug session. Record the item and leave the code as it is.

## Cold-Code Notes

- `perfburn/query/reportgen.py:51-54` — `render` calls `summarize(conn, day)` four times, and each call repeats the same `GROUP BY destination, region` query over one day of rows. Three of the four calls are waste. The report runs once per day via cron, so the saving is one third of a daily job. Hold the result in a variable and pass it to the four render functions. The fix is one line.
- `perfburn/migrate.py:33-38` — the migration loop calls `conn.commit()` after every single row insert. One fsync costs 1 to 10 ms, which caps the migration at a few hundred rows per second. `migrate` is a one-shot script that an operator runs during upgrades. Move the commit after the loop, or use `executemany`.
- `perfburn/config.py:55-58` — `load` compiles 20 regex patterns and runs 20 separate `MULTILINE` scans over the whole config text, one for each key in `DEFAULTS`. One pass with a single line parser would do the same work. `load` runs once at process start.
- `perfburn/cli.py:16-17` — `resolve_alias` builds the list `[command] + aliases` inside the loop over `COMMANDS`, and it calls `.lower()` on each candidate for every comparison. The nested loop runs at most 4 × 5 = 20 times, once per CLI invocation.
- `perfburn/app.py:33` and `perfburn/store/db.py:11` — `main` opens one SQLite connection for the searcher, and `EventStore` opens a second one to the same file. Each connection holds its own page cache. This happens once at startup.
- `perfburn/store/archive.py:19` — `prune` sorts the whole result of `os.listdir(archive_dir)` to drop the oldest entries. The directory holds about `retention_days` files, which `config.py:15` sets to 30. `prune` runs once per hour.
- `tests/test_pipeline.py:10-15` — `make_events` builds a 10,000-line string with `blob +=` in a loop. `"".join()` over a list would avoid the repeated reallocation. The test suite runs in CI, and this helper adds a small fixed cost to one test.

## Not Found

- **Nested loops over the same collection.** The hot path holds no O(N²) loop over one collection. The two quadratic risks are the list scan in `Deduper.seen`, which the P1 finding covers, and the row-by-tag loop in `match_tags`, which the P1 search finding covers.
- **String concatenation in a loop.** `parser.normalize` at `perfburn/ingest/parser.py:23-26` builds the canonical string with `canonical +=`, and `_insert_sync` calls it once per event at `perfburn/store/db.py:25`. I dismissed this one on the numbers. The field count is about 7 to 20, and CPython resizes the string in place when the reference count is 1, so the loop stays near linear. The estimated cost is about 3 µs per event, or under 2% of the 163 µs path that remains after the two top fixes.
- **A lock taken per item in a loop.** The package holds no explicit lock. The only serialization point is the SQLite connection mutex, which the batch commit finding covers.
- **Preallocation of a grown list.** `process_batch` appends to `results` at `perfburn/ingest/pipeline.py:49`, and `_pending_metrics` grows at `perfburn/store/db.py:32`. Python lists grow with a geometric policy, so the amortized cost is small. `_pending_metrics` already flushes at 200 rows, as `config.py:18` sets.
- **Per-item metric flushes.** `queue_metric` at `perfburn/store/db.py:31-34` batches the metric rows and flushes at 200 rows. The pipeline calls it once per batch, not once per event, at `perfburn/ingest/pipeline.py:69`. This code already batches correctly.
- **A map keyed by dense small integers.** `_CONTROL` at `perfburn/ingest/parser.py:7` maps the 32 control code points to `None`. `str.translate` needs a mapping, so a list would not work here. The author already hoisted it to module level.
- **Debug timers or tracing on the serving path.** I found none.