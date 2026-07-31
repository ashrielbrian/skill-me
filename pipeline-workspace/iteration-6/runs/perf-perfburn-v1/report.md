I could not execute Python in this environment, so every number below is an arithmetic estimate from the latency table and standard CPython costs, with the assumptions stated in each `Estimate` field.

# Performance Audit Report

## Summary

`perfburn` is a telemetry service. It parses log lines, removes duplicates, adds source and region data, writes rows to SQLite, and serves tag searches. The hot path is `Pipeline.run()` at `perfburn/ingest/pipeline.py:61`, which the README rates at 5,000 events per second (`README.md:10`); a second warm path is `Searcher.search()` at `perfburn/query/search.py:34`. The best quick win is `Deduper.seen()` (`perfburn/ingest/dedupe.py:9`): it scans a list of 50,000 event ids for each event, which costs about 1.5 ms per event and caps the pipeline at about 660 events per second on its own.

At 5,000 events per second, one core gives a budget of 200 µs for each event. The per-event costs in this report add up to about 2.4 ms of CPU plus one disk flush. The pipeline is therefore about 12 times over its CPU budget before the disk cost.

## Findings

### [P1] Deduper.seen scans a list of 50,000 event ids for each event

- **Category**: Algorithms
- **Location**: `perfburn/ingest/dedupe.py:9-15`. The capacity default is at `perfburn/config.py:18`.
- **Hot path**: `Pipeline.process_one()` calls `self._deduper.seen()` once for each event (`perfburn/ingest/pipeline.py:36`). `process_batch()` calls `process_one()` for each line in the batch (`perfburn/ingest/pipeline.py:46-48`). `run()` is the main loop (`perfburn/ingest/pipeline.py:61-70`). The README rates this loop at 5,000 events per second (`README.md:10`).
- **Problem**: `self._seen` is a list, so `event_id in self._seen` is a linear scan. The config sets `dedupe_capacity` to 50,000 (`perfburn/config.py:18`). A new event id is not in the list, so the test compares against all 50,000 entries before it fails. The eviction `self._seen.pop(0)` then moves the other 49,999 pointers down by one slot. Both costs apply to every unique event, and unique events are the normal case.
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
- **Estimate**: This is an estimate. I assume 50,000 entries and about 30 ns for one string compare in the list scan. The scan costs 50,000 x 30 ns = 1.5 ms for each unique event. The budget for 5,000 events per second on one core is 200 µs for each event, so the scan alone is 7.5 times the whole budget. It caps throughput at about 660 events per second. The `pop(0)` call moves 50,000 pointers, which is 400 KB; at 10 GB/s that adds about 40 µs for each event, or 200 ms for each second of wall time. A set membership test costs about 40 ns, so the fix removes about 1.5 ms for each event. That is a saving of about 37,000 times on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the list with a dict that maps `event_id` to `None`. Test membership with `in` on the dict. For eviction, delete the first key, which a plain dict keeps in insert order in Python 3.7 and later. `collections.OrderedDict.popitem(last=False)` also works. Keep the FIFO order, because `tests/test_pipeline.py:41-46` asserts that the oldest id leaves first. Measure the events per second of `run()` before the change and after it.

### [P1] Enricher.tag_region parses the whole region CSV for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/enrich.py:21-30`. The CSV text arrives once at `perfburn/app.py:44` from `read_region_csv()` (`perfburn/app.py:14-19`).
- **Hot path**: `Enricher.enrich()` calls `self.tag_region(event)` for each event (`perfburn/ingest/enrich.py:35`). `Pipeline.process_one()` calls `self._enricher.enrich(event)` for each event (`perfburn/ingest/pipeline.py:39`), inside the loop at `perfburn/ingest/pipeline.py:46-48` and the main loop at `perfburn/ingest/pipeline.py:61`.
- **Problem**: The method builds a `StringIO` object, builds a `csv.reader` object, and parses every row of the region CSV. It then does one dict lookup on the first octet of the IP address and drops the dict. The CSV text does not change after `__init__`, so this is loop-invariant work. The cost grows with the number of regions, but the code needs only one lookup.
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
- **Estimate**: This is an estimate. The file `regions.csv` is not in the repository, so I give two cases. `csv.reader` costs about 1 µs for each short row, and the two `strip()` calls plus the dict store add about 0.3 µs for each row. With 200 rows the method costs about 260 µs for each event, which is 1.3 times the whole 200 µs budget for one event; at 5,000 events per second that is 1.3 s of CPU for each second of wall time. With 20 rows the method costs about 26 µs for each event, or 130 ms for each second of wall time. A prebuilt dict lookup plus one `split()` costs about 0.5 µs. The saving is about 50 times to 500 times on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Parse the CSV text once in `Enricher.__init__` and store the dict as `self._regions`. Keep `tag_region()` to the two lines that split the address and read the dict. Measure the CPU time of `enrich()` for one batch before the change and after it.

### [P1] Pipeline.notify makes a synchronous HTTP call inside the event loop

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/pipeline.py:52-59`, in particular line 59.
- **Hot path**: `Pipeline.run()` awaits `self.notify(event)` for each event whose destination is `alerts` (`perfburn/ingest/pipeline.py:67-68`). `route()` sends every `error` event and every `warn` event to `alerts` (`perfburn/ingest/pipeline.py:26-31`, route table at `perfburn/config.py:29-34`). The main loop runs at 5,000 events per second (`README.md:10`).
- **Problem**: `notify()` is a coroutine, but `urllib.request.urlopen` is synchronous. The call does not yield to the event loop. The whole loop stops for the full round trip of the webhook. No other event moves through the pipeline during that time, and `submit()` cannot drain the queue. The timeout default is 5 s (`http_timeout_s` at `perfburn/config.py:21`), so one slow webhook stops all ingest for 5 s. The queue holds 10,000 lines (`queue_depth` at `perfburn/config.py:11`), which is 2 s of traffic at 5,000 events per second, so the queue fills and `submit()` blocks the producer.
- **Evidence**:
  ```python
  timeout = self._config.get_int("http_timeout_s")
  urllib.request.urlopen(url, data=payload, timeout=timeout)
  ```
- **Estimate**: This is an estimate. It applies only when the operator sets `webhook_url`; the default is the empty string (`perfburn/config.py:10`), and then `notify()` returns at line 55 for a cost of about 0.1 µs. I assume the operator sets the webhook, that 1% of events are alerts, and that one webhook call takes 20 ms. That gives 50 calls per second x 20 ms = 1.0 s of stall for each second of wall time, so the pipeline stops. One timeout at 5 s drops about 25,000 events of headroom, which is 2.5 times the queue depth of 10,000. A move to a worker thread removes the stall from the loop thread and keeps the throughput at the rate that the other fixes allow.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the call with `await asyncio.to_thread(...)` as the one-line fix. Better, collect the alert events for the batch and send them with `asyncio.gather` over worker threads. Best, use an async HTTP client and do not block the loop at all. Also decide if a webhook failure must stop ingest; the current code raises and kills `run()`. Measure the event loop lag with `asyncio` debug mode before the change and after it.

### [P1] EventStore commits and hops to a thread once for each event

- **Category**: I/O and Round Trips
- **Location**: `perfburn/store/db.py:20-29`, in particular the `commit()` at line 26 and the `asyncio.to_thread()` at line 29.
- **Hot path**: `Pipeline.process_one()` awaits `self._store.insert_event(enriched)` for each event (`perfburn/ingest/pipeline.py:41`). `process_batch()` awaits `process_one()` for each line in order, so the inserts do not overlap (`perfburn/ingest/pipeline.py:46-48`). `run()` is the main loop at 5,000 events per second (`perfburn/ingest/pipeline.py:61`, `README.md:10`).
- **Problem**: There are three costs for each event. First, `_insert_sync()` calls `self._conn.commit()` after one row. SQLite runs in WAL mode (`perfburn/store/db.py:12`) and the code does not set `PRAGMA synchronous`, so the default is `FULL` and each commit does one fsync. Second, each insert pays one `asyncio.to_thread` round trip: the loop submits work to the thread pool, waits, and wakes on the callback. Third, the loop awaits each insert in turn, so no two inserts overlap and the pipeline gets no benefit from the thread.

  A second effect follows. `flush_metrics()` also commits (`perfburn/store/db.py:42`), and `queue_metric()` runs on the loop thread (`perfburn/ingest/pipeline.py:69`). With `metric_flush_rows` at 200 (`perfburn/config.py:18`) and one metric for each batch, this puts one fsync on the loop thread every 200 batches. A third effect: the `Enricher` reads through the same connection object (`perfburn/app.py:44`, `perfburn/store/db.py:11`), so a read waits behind an in-flight commit.
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
- **Estimate**: This is an estimate. One commit with an fsync costs 1 ms to 10 ms. One commit for each event caps throughput at 100 to 1,000 events per second, against the target of 5,000. One `asyncio.to_thread` round trip costs about 100 µs, which is 5,000 x 100 µs = 0.5 s of CPU for each second of wall time, or 50% of one core. The code does not read `batch_size`; the batch size is the length of the list that the caller passes to `submit()` (`perfburn/ingest/pipeline.py:23`). I use the config value of 500 as the assumption (`perfburn/config.py:8`). One `executemany` plus one commit for each batch of 500 cuts the fsync count from 5,000 per second to 10 per second, which is a factor of 500. It also cuts the thread hops from 5,000 per second to 10 per second and saves about 0.5 s of CPU for each second.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Add `insert_events(events)` to `EventStore`. Build the row tuples, call `executemany`, then call `commit()` once. Change `process_batch()` to collect the enriched events and to call `insert_events()` once for the batch. Use one `asyncio.to_thread` call for the whole batch. Also set `PRAGMA synchronous=NORMAL`, which is safe in WAL mode against a process crash but not against a power loss. Give the `Enricher` its own read-only connection. Measure the commits per second and the events per second before the change and after it.

### [P1] Searcher.load_rows reads a whole day of rows and filters them in Python

- **Category**: Algorithms
- **Location**: `perfburn/query/search.py:20-23`, `perfburn/query/search.py:25-32`, and `perfburn/query/search.py:34-44`.
- **Hot path**: `search()` calls `load_rows()` first (`perfburn/query/search.py:35`). `app.main()` builds the `Searcher` and returns it in the wiring dict (`perfburn/app.py:49`). The caller of `search()` is outside this repository, so I take the frequency from the README: the query module serves a few searches per second (`README.md:11`).
- **Problem**: `load_rows()` runs `SELECT * FROM events WHERE day = ?` and materializes every row of that day as a dict. The index `idx_events_day` makes the day lookup fast (`perfburn/migrate.py:12`), but the result set is still the whole day. `match_tags()` then filters in Python, and it calls `row["tags"].split(",")` again for each tag in the query (`perfburn/query/search.py:29`). The code needs at most 100 rows, because `search_page_size` is 100 (`perfburn/config.py:19`) and the slice at line 44 keeps 100 rows. The database can do the filter and the limit, and the row dicts hold all six columns when the result needs only four.
- **Evidence**:
  ```python
  def load_rows(self, day):
      cur = self._conn.execute("SELECT * FROM events WHERE day = ?", (day,))
      columns = [d[0] for d in cur.description]
      return [dict(zip(columns, row)) for row in cur.fetchall()]
  ```
  ```python
  for tag in tags:
      if tag in row["tags"].split(","):
  ```
- **Estimate**: This is an estimate. Ingest runs at 5,000 events per second (`README.md:10`), so one day holds 5,000 x 86,400 = 432,000,000 rows. One `dict(zip(columns, row))` costs about 2 µs and about 400 bytes. For a full day that is about 864 s of CPU and about 170 GB of memory, so the process cannot complete the call. Even one hour of data, which is 18,000,000 rows, costs about 36 s and about 7 GB. The split in `match_tags()` adds about 1 µs for each row and for each tag, so 3 tags over 18,000,000 rows add about 54 s. A tag index plus `LIMIT 100` returns the page in about 1 ms. The saving is more than 10,000 times, and it removes an unbounded memory risk.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Push the filter into SQL. Add a table that maps a tag to an event id, and index it on `(tag, day)`. Then run one query with the day, the tags, and `LIMIT`. Select only the four columns that line 42 reads. Keep the score step, but apply it to the page, not to the day. If a schema change is too large now, add `LIMIT` plus a `tags LIKE ?` filter as a smaller first step. Profile the query against a table with one day of rows before you commit to the schema change.

### [P2] LatencyTracker.observe sorts a 1,024 sample window for each event

- **Category**: Observability Overhead
- **Location**: `perfburn/metrics.py:12-17`. The window default is at `perfburn/config.py:16`.
- **Hot path**: `Pipeline.run()` calls `self._tracker.observe(...)` for each event in the batch (`perfburn/ingest/pipeline.py:66`). It reads `self._tracker.p95` once for each batch (`perfburn/ingest/pipeline.py:69`).
- **Problem**: `observe()` sorts the whole sample window on every call. The loop reads `p95` once for each batch, not once for each event. The code therefore computes and drops one p95 value for every event except the last one in the batch. Two more points: the tracked value is `len(event["fields"])`, which is a field count and not a latency, and nothing in this repository reads the `batch_p95` rows that `flush_metrics()` writes (`perfburn/store/db.py:39-42`).
- **Evidence**:
  ```python
  def observe(self, value):
      self._samples.append(value)
      self.count += 1
      ordered = sorted(self._samples)
      index = int(len(ordered) * 0.95)
      self.p95 = ordered[min(index, len(ordered) - 1)]
  ```
- **Estimate**: This is an estimate. The window is 1,024 samples (`perfburn/config.py:16`). A sort of 1,024 integers needs about 10,240 compares and costs about 30 µs, and the deque-to-list copy adds about 5 µs. At 5,000 events per second that is 5,000 x 35 µs = 175 ms of CPU for each second of wall time, or about 18% of one core. With 500 events in a batch, 499 of the 500 sorts are waste, which is 99.8%. One sort for each batch costs 10 x 35 µs = 0.35 ms for each second. The saving is about 174 ms for each second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Make `p95` a property that sorts on read. Keep `observe()` to the append and the counter. The window is a `deque` with `maxlen`, so the read gives the same answer. The tracked values are small field counts, so a count array over a fixed range gives an O(1) p95 if the read rate later grows. Measure the CPU time of `run()` for one batch before the change and after it.

### [P2] Enricher.enrich deep-copies the event for each event

- **Category**: Allocation and Copies
- **Location**: `perfburn/ingest/enrich.py:32-37`, in particular line 33.
- **Hot path**: `Pipeline.process_one()` calls `self._enricher.enrich(event)` for each event (`perfburn/ingest/pipeline.py:39`), inside the loop at `perfburn/ingest/pipeline.py:46-48` and the main loop at `perfburn/ingest/pipeline.py:61`.
- **Problem**: `copy.deepcopy` walks the object graph, keeps a memo dict, and dispatches for each node. The event holds one string id and one flat dict of strings (`perfburn/ingest/pipeline.py:35`). Strings are immutable, so the deep copy gives no protection that a shallow copy does not give. `enrich()` only adds three top-level keys (lines 34-36) and never writes to `fields`. The later readers also only read: `perfburn/ingest/pipeline.py:57`, `perfburn/ingest/pipeline.py:66`, and `perfburn/store/db.py:24-25`.
- **Evidence**:
  ```python
  def enrich(self, event):
      enriched = copy.deepcopy(event)
  ```
- **Estimate**: This is an estimate. I assume 6 fields in a typical event. `copy.deepcopy` of a two-level dict with 6 string values costs about 10 µs. At 5,000 events per second that is 50 ms of CPU for each second of wall time, or 5% of one core. `dict(event)` costs about 0.3 µs, so the saving is about 9.7 µs for each event, or about 48 ms for each second. That is a factor of about 30 on this step.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace line 33 with `enriched = dict(event)`. If you want stronger isolation of the fields, use `dict(event, fields=dict(event["fields"]))`, which still costs about 1 µs. Confirm first that no caller writes to `fields` after `enrich()`. Measure the CPU time of `enrich()` for one batch before the change and after it.

### [P2] Enricher.lookup_source runs one SQLite query for each event with no cache

- **Category**: I/O and Round Trips
- **Location**: `perfburn/ingest/enrich.py:13-19`. The unused cache setting is at `perfburn/config.py:24`.
- **Hot path**: `Enricher.enrich()` calls `self.lookup_source(...)` for each event (`perfburn/ingest/enrich.py:34`), which `Pipeline.process_one()` calls for each event (`perfburn/ingest/pipeline.py:39`), inside the main loop at `perfburn/ingest/pipeline.py:61`.
- **Problem**: The query runs for each event, on the event loop thread, and it does not yield. The `sources` table is small and it changes rarely; the test data uses 5 source ids (`tests/test_pipeline.py:13`). The config has a `source_cache_rows` key with the value `0`, so someone planned a cache and left it off (`perfburn/config.py:24`). A second effect: this read uses the same connection object as the writer thread (`perfburn/app.py:44`, `perfburn/store/db.py:11`), so it can wait behind an in-flight commit of 1 ms to 10 ms. That effect goes away with the P1 batch commit fix.
- **Evidence**:
  ```python
  cur = self._conn.execute(
      "SELECT name, tier FROM sources WHERE id = ?", (source_id,))
  row = cur.fetchone()
  ```
- **Estimate**: This is an estimate. SQLite is local, so this is not a network round trip. One primary key lookup with a cached statement costs about 10 µs, and the `fetchone()` plus the dict build add about 2 µs. At 5,000 events per second that is 5,000 x 12 µs = 60 ms of CPU for each second of wall time, or 6% of one core. A dict cache lookup costs about 0.1 µs. With a small number of distinct sources the hit rate is near 100%, so the saving is about 59 ms for each second. That is a factor of about 100 on this step.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a dict cache in the `Enricher` that maps `source_id` to the result. Bound it with the `source_cache_rows` value, and treat `0` as "no limit" or as "off", as you prefer. A stale source name is the trade-off; add a TTL or a reload hook if the `sources` table changes while the process runs. Measure the queries per second on the `sources` table before the change and after it.

### [P2] The debug log call builds a JSON string even when debug output is off

- **Category**: Observability Overhead
- **Location**: `perfburn/ingest/pipeline.py:38`.
- **Hot path**: `Pipeline.process_one()` runs this line for each event that is not a duplicate (`perfburn/ingest/pipeline.py:33-42`), inside the loop at `perfburn/ingest/pipeline.py:46-48` and the main loop at `perfburn/ingest/pipeline.py:61`.
- **Problem**: Python evaluates `json.dumps(event)` before it calls `logger.debug`. The lazy `%s` form defers the string interpolation, but it does not defer the argument. The default log level is `INFO` (`perfburn/config.py:12`), so the logger drops the record and the process pays the full serialization cost for nothing.
- **Evidence**:
  ```python
  logger.debug("accepted event %s", json.dumps(event))
  ```
- **Estimate**: This is an estimate. `json.dumps` of a two-level dict with 6 string values costs about 4 µs, and the throw-away string allocation adds pressure on the allocator. At 5,000 events per second that is 20 ms of CPU for each second of wall time, or 2% of one core. This is 0.2% of the current 2.4 ms path cost for each event. After the P1 fixes the path cost drops to about 100 µs for each event, and this line then becomes about 4% of it. The saving is 20 ms for each second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Guard the call with `if logger.isEnabledFor(logging.DEBUG):`. As an alternative, pass an object whose `__str__` method calls `json.dumps`, so the cost moves into the formatter. Measure the CPU time of `process_one()` with the log level at `INFO` before the change and after it.

### [P2] app.main builds a tag index that no code reads

- **Category**: Redundant Work
- **Location**: `perfburn/app.py:22-28` and `perfburn/app.py:34`. The value leaves at `perfburn/app.py:52`.
- **Hot path**: `app.main()` calls `build_query_index(conn)` on every start, for every mode (`perfburn/app.py:34`). `cli.main()` calls `app.main()` for the `ingest` command and for the `query` command (`perfburn/cli.py:41-44`).
- **Problem**: `build_query_index()` scans the whole `events` table, splits the tags of each row, and builds a dict of lists. The function returns the dict and `main()` puts it in the wiring dict under the key `index`. No other code reads that key. `Searcher` takes only the connection (`perfburn/app.py:49`, `perfburn/query/search.py:17-18`). A grep over the package finds writes at `perfburn/app.py:23`, `perfburn/app.py:27`, `perfburn/app.py:34`, and `perfburn/app.py:52`, and no read. The scan also has no `LIMIT`, so its cost and its memory both grow with the table without a bound.
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
- **Estimate**: This is an estimate. Ingest writes 432,000,000 rows for each day at 5,000 events per second. `fetchall()` alone holds every row in memory, and each row plus each list entry costs about 100 bytes, so one day of data needs more than 40 GB and about 400 s of CPU. With a small table of 1,000,000 rows the scan still costs about 1 s and about 100 MB. For the long-running ingest process this cost lands once for each restart. For `python -m perfburn.cli query` the cost lands once for each invocation, and the README rates queries at a few per second (`README.md:11`). Raise this finding to P1 if the `query` command runs once for each user query. The fix removes 100% of this work.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete `build_query_index()` and the `index` key from the wiring dict. If a tag index is a planned feature, build it in the database as a table, as the P1 search finding describes. Measure the wall time of `app.main()` against a populated database before the change and after it.

### [P3] parse_line compiles the regex and translates control characters on each call

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/parser.py:14-20`, in particular line 15 and line 17. `sanitize()` is at `perfburn/ingest/parser.py:10-11`.
- **Hot path**: `Pipeline.process_one()` calls `parser.parse_line(line)` for each event (`perfburn/ingest/pipeline.py:34`), inside the loop at `perfburn/ingest/pipeline.py:46-48` and the main loop at `perfburn/ingest/pipeline.py:61`.
- **Problem**: The pattern is a module constant, but the code compiles it inside the function. The internal `re` cache makes the repeat compile cheap, but it is not free: the call still pays a function call, a type check, and a dict lookup on a tuple key. `sanitize()` calls `str.translate` with a dict of 32 keys, which does one dict lookup for each character of the line.
- **Evidence**:
  ```python
  def parse_line(line):
      pattern = re.compile(FIELD_PATTERN)
      fields = {}
      for match in pattern.finditer(sanitize(line)):
  ```
- **Estimate**: This is an estimate. The `re.compile` cache hit costs about 0.4 µs. I assume a line of 90 characters; `str.translate` with a dict then costs about 3 µs. Together that is 3.4 µs for each event, or 5,000 x 3.4 µs = 17 ms for each second of wall time. That is 1.7% of one core and about 0.14% of the current 2.4 ms path cost for each event. A module-level compiled pattern removes the 0.4 µs. The `translate` cost stays unless you drop the sanitize step, so the safe saving is about 2 ms for each second.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Move the compile to module level as `FIELD_RE = re.compile(FIELD_PATTERN)` and use it in the function. Keep `sanitize()` unless you can show that the input never holds control characters; the regex `\S+` already excludes most of them, so the step may be redundant, but that is a correctness question for the input source. Measure the CPU time of `parse_line()` over 10,000 lines before the change and after it.

### [P3] Pipeline.route rebuilds the route dict for each event

- **Category**: Redundant Work
- **Location**: `perfburn/ingest/pipeline.py:26-31`. The route table is at `perfburn/config.py:29-34`.
- **Hot path**: `Pipeline.process_one()` calls `self.route(enriched)` for each event (`perfburn/ingest/pipeline.py:40`), inside the loop at `perfburn/ingest/pipeline.py:46-48` and the main loop at `perfburn/ingest/pipeline.py:61`.
- **Problem**: The method builds a dict from `self._config.routes` on every call. `Config` sets `routes` once at load time and no code writes to it after that (`perfburn/config.py:38-39`, `perfburn/config.py:59`). The table holds 4 entries, so the waste is small but it is pure overhead.
- **Evidence**:
  ```python
  def route(self, event):
      routes = {}
      for entry in self._config.routes:
          routes[entry["match"]] = entry["destination"]
  ```
- **Estimate**: This is an estimate. The dict build costs about 0.15 µs for each of the 4 entries plus about 0.1 µs of loop overhead, so about 0.7 µs for each event. At 5,000 events per second that is 3.5 ms for each second of wall time, which is 0.35% of one core. A prebuilt dict `get()` costs about 0.05 µs, so the saving is about 3.2 ms for each second.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build the route map once in `Pipeline.__init__` and store it as `self._routes`. Keep `route()` to the two lines that read the level and the map. Measure the CPU time of `process_one()` for one batch before the change and after it.

### [P3] Searcher.search sorts every matched row to return 100 rows

- **Category**: Algorithms
- **Location**: `perfburn/query/search.py:43-44`.
- **Hot path**: `search()` runs for each user query; the README rates this at a few queries per second (`README.md:11`). The caller is outside this repository. The `Searcher` object comes from `perfburn/app.py:49`.
- **Problem**: The code sorts the full `scored` list and then keeps the first 100 entries. A full sort is O(M log M) in the number of matched rows. `heapq.nlargest(100, scored)` is O(M log 100), and it also uses less memory. This finding is second to the P1 search finding: the fix there pushes the filter and the limit into SQL, which makes M small and makes this point moot.
- **Evidence**:
  ```python
  scored.sort(reverse=True)
  return scored[:page_size]
  ```
- **Estimate**: This is an estimate. I assume 1,000,000 matched rows, which is a small fraction of a full day of 432,000,000 rows. A sort of 1,000,000 tuples needs about 20,000,000 compares at about 60 ns for each tuple compare, so about 1.2 s. `heapq.nlargest(100, ...)` needs about 1,000,000 cheap compares against the heap root plus a few thousand heap pushes, so about 0.1 s. The saving is about 1.1 s for each query at that size, and it drops to a few milliseconds after the P1 search fix.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the sort and the slice with `heapq.nlargest(page_size, scored)`. Do the P1 search fix first, because it changes the size of `scored`. Measure the wall time of `search()` against a day with many matched rows before the change and after it.

## Cold-Code Notes

- `perfburn/query/reportgen.py:51-54` — `render()` calls `summarize()` four times, so it runs the same `GROUP BY` query over a full day four times. It runs once per day via cron (`README.md:12`). Call `summarize()` once and pass the result to the four render functions.
- `perfburn/store/archive.py:8-9` — `compress_day()` reads the whole file into memory before it writes the gzip file. It runs once per hour (`README.md:13`). A database file of several GB needs that much memory. Copy in chunks with `shutil.copyfileobj`.
- `perfburn/migrate.py:33-38` — `migrate()` commits after each legacy row, so it does one fsync per row. It is a one-shot script (`README.md:14`).
- `perfburn/config.py:55-58` — `load()` compiles 20 regex patterns and scans the config text 20 times, once for each key in `DEFAULTS`. It runs once at process start.
- `perfburn/app.py:33` and `perfburn/store/db.py:11` — `main()` opens a second SQLite connection to the same file that `EventStore` opens. It runs once at process start.
- `tests/test_pipeline.py:10-15` — `make_events()` builds a 10,000 line string with `+=`. The test suite is small, so this does not block CI.

## Not Found

- **Data Layout**: no separate finding. The one real item is the row dict per event at `perfburn/query/search.py:23`, and the P1 search finding covers it. The `events` table has an index on `day` (`perfburn/migrate.py:12`), which fits the query in `load_rows()`.
- **Algorithms, checked and dismissed**: `parser.normalize()` at `perfburn/ingest/parser.py:23-27` concatenates strings in a loop, which is quadratic in general. The loop is bounded to the number of fields in one event, which is about 6, so the cost is about 1.5 µs. I do not report it.
- **Redundant Work, checked and dismissed**: `score_term()` at `perfburn/query/search.py:8-13` has an `lru_cache` of 4,096 entries. The key holds `field_length`, which varies with each row, so the hit rate is low and the cache can thrash. The function costs about 1 µs, so the loss is small. Drop `field_length` from the key and divide outside the cached call if you touch this code.
- **I/O and Round Trips, positive note**: `EventStore.queue_metric()` at `perfburn/store/db.py:31-34` already batches the metric rows and flushes every 200 rows. This is the pattern that the P1 event insert finding asks for.