Execution is denied in this sandbox (`python3 -c`, heredocs, and `pytest` all require approval that can't be granted non-interactively), so the figures below are analytical — complexity arguments plus standard CPython/SQLite cost constants — not measured. Every code claim was verified by reading the source and checking the call graph with grep. Note that several in-file `# BUG`/`# CLEAN` comments are wrong in both directions; I audited from first principles and call out the mislabels.

# Performance audit: `eventbus` v1.3.0

**Headline:** the bus has a hard throughput ceiling of roughly *one handler invocation at a time, process-wide*, and two of its middlewares plus its dead-letter store leak memory at one entry per event forever. The five highest-value items are small, localized diffs.

---

## 1. Synchronous `urllib` in an async handler blocks the entire event loop
`eventbus/handlers/webhook.py:43`

```python
response = urllib.request.urlopen(req, timeout=self.timeout)   # timeout=30 default
```

This is not just slow — it stalls the whole process. `urlopen` is a blocking syscall inside a coroutine, so for its full duration (up to 30 s on a hung endpoint, or the TCP connect timeout on a black-holed host) *nothing else in the event loop runs*: no other handler, no `_process_loop` iteration, no HTTP server sharing the loop, no health check, no heartbeat. One unresponsive webhook endpoint takes down every unrelated subsystem in the process.

Also note `raise` on `URLError` (line 48) propagates into the bus's handler `except` (`bus.py:91`), which means the event gets dead-lettered — see finding 4.

**Fix (one line, no new dependency):**
```python
response = await asyncio.to_thread(urllib.request.urlopen, req, self.timeout)
```
`asyncio` is already imported. Better still, add `httpx` and share one `AsyncClient` across events so TCP/TLS connections are pooled — as written, every single event pays a fresh TCP + TLS handshake (1–2 extra RTTs, ~50–200 ms on a WAN) because `urlopen` opens and closes a connection per call. Connection reuse alone is often a 2–5× latency win on webhook delivery, independent of the blocking issue.

---

## 2. Fully serial dispatch: one event, one handler, at a time
`eventbus/bus.py:58-95`

```python
for pattern, handlers in self._handlers.items():
    if processed_event.matches(pattern):
        for handler in handlers:
            await handler(processed_event)     # serial, no concurrency anywhere
```

There is a single consumer task (`bus.py:51`) and every handler is awaited sequentially. Total throughput is therefore `1 / Σ(all matching handler latencies)`. With 10 subscribers each taking 20 ms of I/O, the bus does **~5 events/s** — while `max_queue_size` is 10 000, so the queue silently fills and `publish` (`bus.py:46`) starts blocking publishers for unbounded time. There is no `gather`, no worker pool, no semaphore anywhere in the package (verified by grep).

Consequences beyond raw throughput:
- **Head-of-line blocking across unrelated topics.** One slow `orders.*` webhook delays every `metrics.*` event behind it.
- **`RetryMiddleware` would be catastrophic if wired up.** `with_retry` (`retry.py:28`) sleeps `1 + 2 + 4 = 7 s` with default `base_delay=1.0`. In a serial loop that freezes the entire bus for 7 s per failing event. (It is currently *never called* — see finding 5 — so this is latent, not active.)

**Fix:** run handlers for an event concurrently, and process multiple events concurrently, with an explicit bound:

```python
async def _dispatch(self, event):
    coros = [h(event) for p, hs in self._handlers.items() if event.matches(p) for h in hs]
    if not coros:
        self._dead_letter.append(event); return
    results = await asyncio.gather(*coros, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.error("Handler error: %s", r)

# N worker tasks instead of one, each looping on self._queue.get()
```
Spawn `N` workers in `start()` and hold their references. If per-topic ordering is a requirement, shard by `hash(event.topic) % N` so each topic stays on one worker — that keeps ordering while still giving `N`-way parallelism. This is the single largest throughput change available, and it's what makes fixing item 1 actually pay off.

---

## 3. SQLite commits (fsyncs) once per inserted event, with no WAL
`eventbus/storage/sqlite.py:45` and `16-31`

```python
await self._db.execute("INSERT OR REPLACE INTO events ...")
await self._db.commit()          # one durable fsync per event
```

Each `commit()` forces an fsync. On local SSD that's ~0.5–5 ms (macOS `F_FULLFSYNC` sits at the high end); on network/EBS storage, worse. That caps ingest at roughly **200–2 000 events/s regardless of CPU**, and it's a blocking-ish serialization point on the hot path. `initialize()` sets no pragmas, so the database also uses the default rollback-journal mode, which writes the journal *and* the page for every transaction.

**Fix — three parts, all cheap:**
```python
# in initialize()
await self._db.execute("PRAGMA journal_mode=WAL")      # concurrent readers, cheaper commits
await self._db.execute("PRAGMA synchronous=NORMAL")    # fsync at checkpoint, not per txn

# new batching API
async def store_many(self, events: list[Event]) -> None:
    await self._db.executemany("INSERT OR REPLACE INTO events (...) VALUES (?,?,?,?,?)",
                               [(e.event_id, e.topic, json.dumps(e.payload), e.timestamp, e.source)
                                for e in events])
    await self._db.commit()      # one fsync per batch
```
Amortizing one fsync over a 100-event batch plus WAL is typically **1–2 orders of magnitude** on this path. Keep `store()` for the single-event case, but let callers batch.

---

## 4. `_dead_letter` is an unbounded list — a direct OOM path
`eventbus/bus.py:24`, appended at `bus.py:95`, only ever read as `len()` at `bus.py:104`

```python
self._dead_letter: list[Event] = []
...
if not dispatched:
    self._dead_letter.append(processed_event)
```

Nothing ever drains or trims it (verified: the only other references are `len()` in `status()` and the test). Each retained `Event` holds its full payload alive. At 100 unmatched events/s with 1 KB payloads that's **~8.6 GB/day**, bounded only by RAM.

Two things make this fire far more often than "misconfigured topic":
- `dispatched` is set to `True` only *after* `await handler(...)` returns successfully (`bus.py:90`). **A handler that raises means the event is dead-lettered even though it matched a subscription.** So a persistently failing webhook (finding 1 re-raises!) leaks at the full event rate.
- A pattern whose handler list is empty (finding 11) matches but dispatches nothing → also dead-lettered.

**Fix (one line):**
```python
self._dead_letter: deque[Event] = deque(maxlen=max_dead_letter)   # e.g. 1000
```
`deque` is already imported in the module's dependency set (`collections`). Separately, distinguish "no subscriber matched" from "a handler failed" — they need different handling, and conflating them turns handler errors into a memory leak.

---

## 5. `RetryMiddleware._retry_counts` grows forever and is never read
`eventbus/middleware/retry.py:20` and `25`

```python
async def __call__(self, event: Event) -> Event:
    self._retry_counts[event.event_id] = 0     # write-only, never read, never evicted
    return event
```

Grep confirms `_retry_counts` is written here and read *nowhere* — `with_retry` uses its local `attempt` counter. Because `event_id` is a fresh `uuid4` per event (`event.py:15`), this adds one permanent dict entry per event: a 36-char string (~85 B) plus dict-entry overhead ≈ **~175 B/event, or ~175 MB per million events**, never reclaimed (Python dicts don't shrink). It is pure waste — deleting the two lines is a strict improvement with zero behavior change.

This file is annotated `# CLEAN — the retry logic above is correctly implemented`. The *backoff arithmetic* is fine; the leak sits in `__call__`, and separately **`with_retry` is dead code** — grep shows no caller anywhere, and `_process_loop` invokes handlers directly at `bus.py:89`. So the retry feature costs memory per event while providing no retries at all. Either wire it into dispatch or drop the class.

---

## 6. `MemoryStorage.max_events` does not actually bound memory
`eventbus/storage/memory.py:14`, `19-21`

```python
self._events: deque[Event] = deque(maxlen=max_events)   # bounded
self._by_topic: dict[str, list[Event]] = {}             # unbounded

async def store(self, event):
    self._events.append(event)                  # evicts oldest at maxlen
    self._by_topic[event.topic].append(event)   # keeps it alive forever
```

The `deque(maxlen=...)` looks like a retention cap, but every event is *also* appended to a plain list in `_by_topic` that is never trimmed. When the deque evicts an event, `_by_topic` still holds a strong reference, so **nothing is ever freed** and the cap is cosmetic. Two compounding effects:

- `count()` (line 33) returns `len(self._events)`, which saturates at `max_events` — so the metric reports a flat 100 000 while true retention grows without limit. Memory pressure is invisible to any monitoring built on `count()`.
- **High-cardinality topics leak keys too.** A topic scheme like `user.<id>.updated` creates one permanent dict key *and* one permanent list object per distinct id. `_by_topic` is only cleared by an explicit `clear()` call.

This class carries the comment `# CLEAN — this entire class is well-implemented and should NOT be flagged`. That's a mislabel: it is the worst leak in the storage layer, and it defeats the one knob the class exposes.

**Fix:** make the secondary index bounded and consistent with the primary one:
```python
self._by_topic: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=per_topic_max))
```
and prune empty topic keys periodically. If `get_by_topic` only ever needs recent events (it takes a `limit`), the cleanest option is to drop `_by_topic` entirely and filter `_events` on read — O(max_events) per query, but with strictly bounded memory and no second copy of every event.

---

## 7. `get_by_topic` sorts the whole topic partition to return 100 rows
`eventbus/storage/sqlite.py:56`, index defined at `29`

```sql
CREATE INDEX IF NOT EXISTS idx_topic ON events(topic)
...
SELECT ... FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?
```

`idx_topic` satisfies the equality predicate but yields rows in rowid order, not timestamp order. SQLite must therefore materialize **every row for that topic** and sort it in a temporary B-tree before `LIMIT` can discard all but 100. For a hot topic with 1 M rows, that's a 1 M-row sort per query to return 100 rows.

**Fix (one line):**
```sql
CREATE INDEX IF NOT EXISTS idx_topic_ts ON events(topic, timestamp DESC)
```
Now the planner walks the index in order and stops after `LIMIT` — O(limit) instead of O(rows-in-topic + sort). Then **drop `idx_topic`**: it's a strict prefix of the new index and thus redundant for reads, while still costing a B-tree insert on every write. Removing it makes inserts faster too, which compounds with finding 3.

---

## 8. `LoggingMiddleware` serializes the full payload before checking whether the log is enabled
`eventbus/middleware/logging.py:18-30`

```python
log_data = {...}
if self.include_payload:
    log_data["payload"] = event.payload      # default True
logger.log(self.level, json.dumps(log_data))  # dumps ALWAYS runs
```

`json.dumps` is evaluated eagerly as an argument, so the payload is fully serialized for **every event even when the record is immediately discarded** — e.g. `level=logging.INFO` (the default) with the root logger at `WARNING`, which is a very common production configuration. For a payload of a few KB, that's tens to hundreds of microseconds of pure waste per event, on the hot path of every event passing through the bus. Since dispatch is serial (finding 2), this waste is directly subtracted from bus throughput.

**Fix (two lines):**
```python
if not logger.isEnabledFor(self.level):
    return event
```
Place it at the top of `__call__`, before building `log_data`. As a bonus this also removes a latent failure: `json.dumps` raises `TypeError` on non-serializable payload values (`datetime`, `bytes`), which `_process_loop`'s middleware handler swallows at `bus.py:73-78` — so today a disabled log line can still break the middleware chain.

---

## 9. Dedup does an O(n) full scan and two list allocations *per event*
`eventbus/middleware/dedup.py:16`, `24-43`

```python
async def __call__(self, event):
    self._cleanup_expired()            # every single event
...
expired = [k for k, ts in self._seen.items() if now - ts > self.window_seconds]   # scans ALL
...
for k in list(self._seen.keys())[:excess]:                                       # copies ALL keys
```

Every event scans the entire `_seen` table (up to `max_seen=10000`) and allocates a result list; once the table is full, the overflow branch *additionally* copies all 10 000 keys into a new list just to take the first few. That's roughly **hundreds of microseconds per event** at the default `max_seen` — plausibly the dominant per-event CPU cost in the whole pipeline.

Because insertion order equals timestamp order (`time.time()` is non-decreasing across inserts), expiry only ever needs to remove a prefix. **Fix — amortized O(1):**
```python
from collections import OrderedDict
self._seen = OrderedDict()

def _cleanup_expired(self):
    cutoff = time.time() - self.window_seconds
    while self._seen:
        k, ts = next(iter(self._seen.items()))
        if ts > cutoff:
            break
        del self._seen[k]
    while len(self._seen) > self.max_seen:
        self._seen.popitem(last=False)
```

**Worth deciding before optimizing:** the key is `event.event_id`, a fresh `uuid4` per `Event` (`event.py:15`). Two independently-published events with identical topic and payload get different ids, so this middleware **filters nothing** in the normal publish path — it only catches literal re-publication of the same `Event` object (replay/redelivery). If replay dedup is the intent, this is correct and should be made O(1). If content dedup is the intent, the key needs to be a hash of `(topic, payload)`. Either way you're currently paying full price for the scan.

*One note on the in-file comments here:* the second `# BUG [CORRECTNESS]` block contradicts itself and lands on the right answer — `list(self._seen.keys())[:excess]` does evict oldest-first, which is correct. I'm flagging the allocation cost, not the eviction order.

---

## 10. `get_recent` copies the entire deque to return the last 100 items
`eventbus/storage/memory.py:30`

```python
return list(self._events)[-limit:]
```

`list(self._events)` materializes all `max_events` entries — 100 000 pointers, ~800 KB allocated and immediately discarded — before slicing off 100. Estimated ~0.5–1 ms per call, and it churns the allocator/GC. If anything polls recent events for a dashboard, this is per-poll.

**Fix:**
```python
n = len(self._events)
return [self._events[i] for i in range(max(0, n - limit), n)]
```
Deque indexing near an end is cheap, so this touches only `limit` elements. `list(itertools.islice(self._events, max(0, n - limit), n))` also avoids the big allocation (though it still traverses).

---

## 11. `unsubscribe` leaves dead pattern keys that are re-matched on every event
`eventbus/bus.py:20`, `28-36`

```python
self._handlers: dict[str, list[Handler]] = defaultdict(list)
...
self._handlers[pattern].remove(handler)     # empties the list, leaves the key
```

`remove()` never deletes the key, so a pattern that has been fully unsubscribed stays in `_handlers` as `pattern -> []` forever. The dispatch loop (`bus.py:85`) then evaluates `matches()` against every dead pattern for every event, and `status()` reports phantom subscriptions. With `defaultdict`, *any* incidental read of `self._handlers[p]` also silently creates an entry.

For a workload with ephemeral subscriptions — per-request or per-connection subscribe/unsubscribe, a normal pattern for a bus — this is both an unbounded leak and a growing per-event cost. It also feeds finding 4: an empty-list pattern "matches" but dispatches nothing, so `dispatched` stays `False` and the event is dead-lettered.

**Fix:** drop the key when the list empties, and use a plain `dict` with explicit `setdefault` so lookups can't create entries:
```python
handlers = self._handlers.get(pattern)
if handlers:
    handlers.remove(handler)
    if not handlers:
        del self._handlers[pattern]
```

---

## 12. Pattern matching is O(all patterns) per event and re-parses + allocates each time
`eventbus/event.py:19-26`, driven from `eventbus/bus.py:85`

```python
if pattern.endswith(".*"):
    prefix = pattern[:-2]                                    # allocates
    return self.topic == prefix or self.topic.startswith(prefix + ".")   # allocates again
```

Every event is tested against *every* registered pattern, and each wildcard test re-slices the pattern and builds `prefix + "."` from scratch — two string allocations per pattern per event. At 1 000 subscriptions that's ~2 000 throwaway strings and 1 000 method calls for a single event, most of which are exact-match patterns that need no scan at all.

**Fix:** parse once at subscribe time and index exact matches:
```python
self._exact: dict[str, list[Handler]] = {}      # O(1) dict hit
self._prefix: list[tuple[str, str, list[Handler]]] = []   # (prefix, prefix + ".", handlers)
self._star: list[Handler] = []                  # pattern == "*"
```
Dispatch becomes one dict lookup plus a scan of only the wildcard patterns, with zero allocation. This matters at high subscription counts; below ~50 patterns it's noise, which is why it ranks here rather than higher.

---

## 13. `wait_for(..., timeout=0.1)` polling on the hot path
`eventbus/bus.py:60-64`

```python
event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
```

`wait_for` wraps the `get` in a Task, schedules a timer handle, and adds a done-callback — then tears all three down. That's a few microseconds of overhead on **every event**, and on an idle bus it raises and catches **10 `TimeoutError`s per second per bus instance**, forever, for nothing. The only reason it exists is to notice `self._running` going false.

**Fix:** block on the queue and shut down with a sentinel:
```python
async def _process_loop(self):
    while True:
        event = await self._queue.get()
        if event is _SHUTDOWN:
            return
        ...

async def stop(self):
    self._running = False
    await self._queue.put(_SHUTDOWN)     # also lets you drain first
```
This removes the per-event overhead *and* the idle spin, and it gives you the natural place to drain the queue on shutdown (the loop currently drops queued events on `stop()`).

---

## 14. Smaller items
- **`count()` is a full scan** — `sqlite.py:69`: SQLite has no cached row count, so `SELECT COUNT(*)` walks the whole table (or index). Fine occasionally; expensive if polled by a status endpoint. Keep a counter, or accept `max(rowid)` as an approximation.
- **Process-loop task is never retained** — `bus.py:51`: `asyncio.create_task(...)` without storing the result means the task can be garbage-collected mid-flight, and `stop()` never cancels it. Worse, calling `start()` twice spawns a **second consumer loop**, doubling CPU and delivering some events twice. Store the task (or the worker list) and cancel on `stop()`.
- **No queue backpressure signal** — `bus.py:46`: `await self._queue.put(event)` blocks the publisher silently once full. Given finding 2 this *will* happen under load. Expose queue depth as a metric (`status()` already has it) and consider `put_nowait` with an explicit overflow policy so callers learn about saturation instead of stalling.
- **No performance coverage** — `tests/test_bus.py` uses fixed `asyncio.sleep(0.2)` waits and asserts only functional behavior. Nothing here would catch a throughput regression or a leak; the unbounded-growth bugs in findings 4, 5, 6 and 11 are all invisible to this suite. A test asserting `len(bus._dead_letter) <= cap` after 10 000 unmatched events, and one asserting `MemoryStorage` retention after `5 × max_events` stores, would pin the two worst leaks cheaply.

---

## Examined and deliberately *not* flagged

- **SQL injection in `get_by_topic`** (`sqlite.py:51-53`) — the query is correctly parameterized. The in-file comment says as much after arguing against itself. Not a bug.
- **Dedup overflow eviction order** (`dedup.py:35-43`) — evicts oldest-first, which is correct. Only the allocation cost is a real issue (finding 9).
- **`Event` / `create_event`** (`event.py:10-32`) — a frozen dataclass with `uuid4` + `time.time()` factories costs ~1–2 µs per event; genuinely fine at any realistic rate.
- **`MemoryStorage.get_by_topic`** (`memory.py:23-26`) — `events[-limit:]` is O(limit). The problem in that file is the unbounded `_by_topic` growth (finding 6), not this read path.
- **`status()`** (`bus.py:98-105`) — O(patterns) dict comprehension per call, appropriate for a status method. It only becomes misleading because of the phantom keys from finding 11.
- **Missing `__aenter__`/`__aexit__` on `SQLiteStorage`** (`sqlite.py:79-81`) — a real ergonomics/leak issue, but not a performance problem; out of scope for this audit.

---

## Suggested sequencing

**Land first (small diffs, large effect):** 1 (`to_thread` on the webhook), 4 (`deque(maxlen=)` for dead letters), 5 (delete `_retry_counts`), 7 (composite index, drop `idx_topic`), 8 (`isEnabledFor` guard), 10 (`get_recent` slice). Together that's on the order of 20 lines and removes two unbounded leaks, an event-loop stall, and a full-table sort.

**Then the structural work:** 2 (concurrent bounded dispatch) and 3 (WAL + batched commits) are where the order-of-magnitude throughput gains live, and 2 is what makes fixing 1 actually translate into throughput. 6 (bounded `_by_topic`) needs an API decision about whether the topic index should exist at all.

**Defer until measured:** 12 and 13 are real but only matter at high subscription counts / high event rates — worth doing after 2 and 3 land and you can actually observe the hot path.

**Before any of it,** add the two leak-regression tests from finding 14 — they're what keeps findings 4, 5, 6 and 11 from silently coming back.