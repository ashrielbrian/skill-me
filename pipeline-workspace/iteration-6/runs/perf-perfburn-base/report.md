`python3 -c` execution requires approval and this session is non-interactive, so I could not run benchmarks. The findings below are from static analysis; complexity claims are exact, and the per-event cost figures are order-of-magnitude estimates from CPython cost models, explicitly labeled as such.

A useful frame throughout: **at the documented 5,000 events/s, one core affords ~200 µs of CPU per event.** Several single functions in the hot path exceed that budget on their own.

---

# perfburn performance audit

## Tier 1 — blockers for the documented 5,000 ev/s

### 1. `Deduper.seen()` is an O(n) list scan over 50,000 entries, per event
`perfburn/ingest/dedupe.py:9-15`

```python
self._seen = []          # list, not set
if event_id in self._seen:   # O(capacity) linear scan
self._seen.pop(0)            # O(capacity) memmove
```

`dedupe_capacity` defaults to 50,000, so after ~10 seconds of ingestion the buffer is full and every **non-duplicate** event — the common case — scans all 50,000 entries before being appended. `pop(0)` then shifts the whole list. Estimated ~1–2 ms of CPU per event: **5–10× the entire per-event budget, from this function alone.**

The fix preserves semantics exactly. Note the eviction policy is FIFO, not LRU (`test_capacity_evicts_oldest` pins this), and ids are only appended when unseen, so entries are unique — a `set` for membership plus a `deque` for order is a drop-in:

```python
from collections import deque

class Deduper:
    def __init__(self, capacity=50000):
        self._seen = set()
        self._order = deque()
        self._capacity = capacity

    def seen(self, event_id):
        if event_id in self._seen:
            return True
        self._seen.add(event_id)
        self._order.append(event_id)
        if len(self._order) > self._capacity:
            self._seen.discard(self._order.popleft())
        return False

    def size(self):
        return len(self._order)
```

O(n) → O(1). Both existing tests pass unchanged. **This is the single highest-value fix in the repo.**

---

### 2. One transaction + one `to_thread` hop per event
`perfburn/store/db.py:20-29`, `perfburn/ingest/pipeline.py:44-50`

```python
def _insert_sync(self, event):
    self._conn.execute("INSERT INTO events ...")
    self._conn.commit()                       # fsync per event

async def insert_event(self, event):
    await asyncio.to_thread(self._insert_sync, event)   # thread hop per event
```

Two compounding costs:

- **`commit()` per event.** SQLite's default `synchronous=FULL` persists under WAL, so each commit fsyncs the WAL. That's a hard ceiling of roughly 1,000–10,000 commits/s on local SSD, and far worse on network or virtualized storage. This is a wall-clock wall, not a CPU one.
- **`to_thread` per event, awaited sequentially.** `process_batch` awaits each `process_one` in turn, so the thread pool provides zero concurrency — it's pure dispatch overhead (~50–150 µs round trip), 25–75% of the per-event budget spent on scheduling.

Batch the whole thing. The store already has this pattern for metrics (`queue_metric`/`flush_metrics`) — apply it to events:

```python
def _insert_many_sync(self, events):
    self._conn.executemany(
        "INSERT INTO events (id, day, destination, region, tags, payload)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(e["id"], e["day"], e["destination"], e["region"],
          e["fields"].get("tags", ""), parser.normalize(e["fields"]))
         for e in events])
    self._conn.commit()

async def insert_events(self, events):
    if events:
        await asyncio.to_thread(self._insert_many_sync, events)
```

`process_batch` then collects enriched events and makes **one** `to_thread` call per batch. At `batch_size=500` that is one fsync and one thread hop per 500 events instead of 500 of each. Also consider `PRAGMA synchronous=NORMAL` alongside WAL — the standard WAL pairing, which trades losing the last few transactions on power loss (not corruption) for dropping the per-commit fsync.

**Related hazard worth fixing at the same time:** `app.py:44` hands `store.connection` to the `Enricher`, so the *same* `sqlite3` connection is used by `lookup_source` on the event-loop thread and by `_insert_sync` on pool threads (`check_same_thread=False`). Python's sqlite3 serializes access internally, so this is not corrupting — but it means **the event loop thread can block on the connection mutex while a worker holds it through an fsync.** Give the enricher its own read connection.

---

### 3. `Enricher.tag_region()` re-parses the entire region CSV on every event
`perfburn/ingest/enrich.py:21-30`

```python
def tag_region(self, event):
    regions = {}
    reader = csv.reader(io.StringIO(self._region_csv_text))   # per event
    for row in reader:                                        # per event
        ...
    return regions.get(octet, "other")                        # one lookup
```

A `StringIO`, a `csv.reader`, and a full dict rebuild — all to service a single lookup keyed on the first IP octet. `_region_csv_text` is immutable, set once in `__init__`. Cost scales with CSV size; for a 200-row file, estimated ~100–200 µs per event, roughly half to all of the per-event budget.

Parse once in the constructor:

```python
def __init__(self, conn, region_csv_text):
    self._conn = conn
    self._regions = {}
    for row in csv.reader(io.StringIO(region_csv_text)):
        if len(row) == 2:
            name, prefix = row
            self._regions[prefix.strip()] = name.strip()

def tag_region(self, event):
    octet = event["fields"].get("ip", "0.0.0.0").split(".")[0]
    return self._regions.get(octet, "other")
```

Per-event cost drops to a dict lookup. (`app.read_region_csv` returning raw text is what invites this shape — passing the parsed mapping would be cleaner still.)

---

### 4. `LatencyTracker.observe()` sorts the full 1024-sample window on every event
`perfburn/metrics.py:12-17`

```python
def observe(self, value):
    self._samples.append(value)
    self.count += 1
    ordered = sorted(self._samples)      # O(w log w) per event
    self.p95 = ordered[...]
```

Estimated ~40–80 µs per event (20–40% of budget) — and it is almost entirely wasted, because `p95` is read exactly **once per batch**, at `pipeline.py:69`. At `batch_size=500` that's 500 sorts to produce one consumed value.

Make it lazy:

```python
@property
def p95(self):
    if not self._samples:
        return 0.0
    ordered = sorted(self._samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]

def observe(self, value):
    self._samples.append(value)
    self.count += 1
```

(Drop the `self.p95 = 0.0` assignment in `__init__`, which would otherwise shadow the property.) Same numbers, ~500× fewer sorts. If p95 ever becomes a per-event read, move to a proper streaming quantile estimator.

**Separately — a correctness note, not a perf one:** `pipeline.py:66` calls `self._tracker.observe(len(event["fields"]))`, feeding the tracker a *field count*, not a latency. The metric written as `batch_p95` currently measures event width. Worth confirming that's intended before optimizing around it.

---

### 5. Blocking `urlopen` inside the async event loop
`perfburn/ingest/pipeline.py:52-59`

```python
async def notify(self, event):
    ...
    urllib.request.urlopen(url, data=payload, timeout=timeout)   # synchronous
```

Declared `async`, but this is a fully blocking call — it stalls the **entire** event loop, including all in-flight store writes. Default routing sends both `error` and `warn` to `alerts`, so at 5,000 ev/s even a 1% alert rate is 50 blocking HTTP round trips per second; at 20 ms RTT that is a saturated loop with zero remaining capacity. A single hung endpoint stalls all ingestion for the full `http_timeout_s` (default 5 s), overflowing the 10,000-deep queue and back-pressuring producers.

Minimum fix — get it off the loop thread:

```python
await asyncio.to_thread(
    urllib.request.urlopen, url, data=payload, timeout=timeout)
```

Better: dispatch notifications as bounded background tasks so batch processing doesn't serialize on them at all, and use a client with connection reuse instead of a fresh TCP+TLS handshake per alert.

*Conditional:* `webhook_url` defaults to `""`, so this is inert until configured — but it becomes severe the moment it is.

---

### 6. `build_query_index()` scans the entire events table at startup, and nothing reads the result
`perfburn/app.py:22-28`, called unconditionally at `app.py:34`

```python
cur = conn.execute("SELECT id, tags FROM events")   # full table scan
for event_id, tags in cur.fetchall():               # entire table into RAM
    for tag in (tags or "").split(","):
        index.setdefault(tag, []).append(event_id)
```

I grepped the repo: the returned `index` is placed in the wiring dict at `app.py:52` and **never read by any consumer**. `Searcher` queries the database directly and does not use it. Meanwhile this runs on *every* startup in *every* mode — including `mode="ingest"`, which has no query path at all.

At the documented ingest rate the events table is very large; this is an unbounded full scan plus a Python dict-of-lists holding every event id, paid before the process can serve anything. **Delete `build_query_index` and its call site.** If a tag index is wanted later, it belongs in SQLite (see #9), not rebuilt in memory at boot.

While there: `conn` (`app.py:33`) is a second connection to the same file, is unused in ingest mode once the index call is gone, and is never closed.

---

## Tier 2 — solid wins, small diffs

### 7. `copy.deepcopy()` per event in `enrich()`
`perfburn/ingest/enrich.py:33`

`deepcopy` is among the slowest common operations in CPython — recursive dispatch plus a memo dict, estimated ~5–10 µs even for this small two-level structure. `enrich()` only *adds* top-level keys (`source`, `region`, `day`), and the caller at `pipeline.py:35` builds `event` as a throwaway local that nothing else retains. Nothing downstream mutates `fields`. A shallow `dict(event)` is sufficient and roughly an order of magnitude cheaper; mutating `event` in place is cheaper still.

### 8. `json.dumps()` runs on every event for a log line that is discarded
`perfburn/ingest/pipeline.py:38`

```python
logger.debug("accepted event %s", json.dumps(event))
```

Logging's lazy `%`-formatting defers *formatting*, not *argument evaluation*. With `log_level` defaulting to `INFO`, the record is thrown away — but the full JSON serialization (~5–10 µs/event) is paid regardless. Guard it:

```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("accepted event %s", json.dumps(event))
```

### 9. `Searcher` pulls a full day of rows and filters in Python
`perfburn/query/search.py:20-44`

Four separate issues in one path:

- `SELECT *` fetches the `payload` column — the largest, containing the entire normalized field blob — which is never used. Select only `id, day, tags`.
- `fetchall()` materializes an entire day's rows, each as a Python dict. At the documented ingest rate a day is enormous; this is a memory blowout per query.
- `match_tags` recomputes `row["tags"].split(",")` once *per query tag* per row (`search.py:29`), inside the inner loop. Split once per row into a set.
- The full matched set is sorted to return `page_size` items — use `heapq.nlargest(page_size, scored)`.

The structural fix is to filter in SQL. Given `tags` is a comma-joined string, the durable answer is a normalized `event_tags(event_id, tag)` table with an index on `tag`, or FTS5; short of that, at least push a `LIKE` predicate into the existing `WHERE day = ?` query so the rows never cross into Python.

### 10. `reportgen.render()` runs the same aggregation query four times
`perfburn/query/reportgen.py:49-55`

```python
sections.append(render_header(summarize(conn, day), day))
sections.append(render_regions(summarize(conn, day)))       # identical query
sections.append(render_destinations(summarize(conn, day)))  # identical query
sections.append(render_top(summarize(conn, day)))           # identical query
```

Four identical `GROUP BY` scans over a day of events for four identical results. One-line fix: `summary = summarize(conn, day)` once, reuse. Only runs daily, so low absolute impact — but it's free, and it's 4× a scan that grows with your data.

### 11. `Enricher.lookup_source()` hits SQLite once per event
`perfburn/ingest/enrich.py:13-19`

`sources.id` is a `PRIMARY KEY` so the lookup is indexed and reasonably fast (~5–15 µs), but it is a *synchronous* database call executed on the event-loop thread, once per event, and it contends on the shared connection mutex (see #2). Source cardinality is low — the config even has an unused `source_cache_rows` key suggesting this was planned. Wrap it in an `lru_cache`, or preload the `sources` table into a dict at startup.

### 12. `archive.compress_day()` reads the whole file into memory
`perfburn/store/archive.py:8-9`

```python
with open(path, "rb") as f:
    data = f.read()          # entire file resident
```

An hourly job that holds a full day's file in RAM before compressing it. Stream instead — constant memory, no other behavior change:

```python
import shutil
os.makedirs(archive_dir, exist_ok=True)
out_path = os.path.join(archive_dir, os.path.basename(path) + ".gz")
with open(path, "rb") as f, gzip.open(out_path, "wb") as out:
    shutil.copyfileobj(f, out)
os.remove(path)
```

---

## Tier 3 — cheap, low impact

| # | Location | Issue | Fix |
|---|---|---|---|
| 13 | `ingest/parser.py:15` | `re.compile()` inside `parse_line`. The `re` module caches internally, so this is a pattern-string hash + dict lookup (~1–2 µs), not a recompile — smaller than it appears, but paid 5,000×/s. | Hoist to a module-level `_PATTERN`. |
| 14 | `ingest/pipeline.py:26-31` | `route()` rebuilds the routes dict from `config.routes` on every event to read one key. | Build the mapping once in `__init__`. |
| 15 | `migrate.py:33-38` | `commit()` inside the per-row insert loop — an fsync per row. `_legacy_rows` also `fetchall()`s the whole legacy table into memory. | `executemany` with a single commit; iterate the cursor instead of `fetchall`. One-shot script, so lowest priority — but it's the difference between seconds and hours on a large table. |
| 16 | `ingest/parser.py:23-27` | `canonical += ...` in a loop. CPython's in-place `str +=` optimization usually applies here, so real cost is modest, but `"".join(...)` is unconditionally better and avoids the quadratic worst case. | `"".join(f"{k}={fields[k]};" for k in sorted(fields))` |

---

## Checked and deliberately not flagged

So you don't spend time here:

- **`config.load()`** compiling ~20 `MULTILINE` regexes — runs once at process start over a small file. Genuinely negligible.
- **`cli.resolve_alias()`** nested loop — 4 commands, once per invocation.
- **`@lru_cache` on `score_term`** (`search.py:8`) — keyed on `(term, field_length)`; `field_length` varies per row so the hit rate is likely poor and the 4096-entry cache mostly churn. Caching on `term` alone (the character loop) and dividing by `1 + field_length` at the call site would be strictly better, but this is noise next to #9.
- **`archive.prune()`** — `sorted(os.listdir(...))` on an archive directory is fine at any plausible size.
- **Schema indexes** — `idx_events_day` exists and covers the day filter; `sources.id` is a `PRIMARY KEY`. The gap is a tag index (#9), not a missing day index.

---

## Suggested sequencing

**Do first (one afternoon, ~40 lines total):** #1 dedupe, #3 CSV parse hoist, #4 lazy p95, #6 delete the dead index, #7 deepcopy, #8 debug guard. These are all small, self-contained, low-risk, and remove the large majority of per-event CPU.

**Do next (needs a real change to the batch path):** #2 batched inserts with a single commit — the biggest remaining win and the one that lifts the fsync ceiling — plus #5 if webhooks are or will be enabled.

**Then:** #9, the query path, which is independent of ingest and can be scheduled separately.

My honest read: as written, the ingest pipeline cannot sustain 5,000 events/s on one core — the per-event CPU in #1, #3, and #4 alone appears to exceed the 200 µs budget by several multiples, before the per-event fsync in #2. Either the production rate is being met by something not in this repo, the dedupe buffer is not reaching capacity in practice, or the documented figure is aspirational. **Measuring `Deduper.seen` under a full 50,000-entry buffer is the fastest way to confirm where you actually stand** — I'd start there before committing to the larger #2 refactor.