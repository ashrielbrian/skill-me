I have read all 501 lines across 15 files and verified the call graph. The sandbox denied the benchmark run, so every number below is an estimate from the audit latency table plus the CPython per-statement rule. I state that in the report.

# Performance Audit Report

## Summary

The `eventbus` package is an in-process async pub/sub bus for Python 3.11. One consumer task, `EventBus._process_loop` at `eventbus/bus.py:58`, is the single hot path: every event passes through the middleware chain at `bus.py:68-70` and then through the handler dispatch scan at `bus.py:85-92`. The package adds three middleware and two storage backends around that loop.

The best quick win is `DeduplicationMiddleware._cleanup_expired` at `eventbus/middleware/dedup.py:24`. The method scans all 10,000 tracked entries one time for each event. It costs about 96% of the per-event CPU time. A front-pop loop makes the cost O(1) and raises the estimated throughput ceiling from about 970 events per second to about 28,000 events per second.

Two separate cost models apply, and the report marks which findings belong to each:

- **In-process handlers.** The per-event cost is CPU only. The dedup scan dominates it.
- **I/O handlers (webhook, SQLite).** One blocking call or one commit for each event dominates all CPU work in the loop.

### Assumptions for every Estimate field

- **A1.** The bus carries 1,000 events per second. The queue limit `max_queue_size=10000` at `bus.py:22` and the dedup limit `max_seen=10000` at `dedup.py:9` give a 10-second buffer at this rate.
- **A2.** A user registers all three middleware. `eventbus/middleware/__init__.py:2-4` exports all three.
- **A3.** A user subscribes 100 distinct patterns.
- **A4.** An event payload holds about 10 keys.
- **A5.** One interpreted CPython statement costs 10–100 ns. This comes from the audit rule of thumb.
- **A6.** No profiler ran. The sandbox blocked the benchmark. Every number is an estimate. Measure before you start a Medium or larger fix.

### Per-event CPU budget (estimate, in-process handlers)

| Step | Location | Cost for each event | Share |
|---|---|---|---|
| `_cleanup_expired` full scan | `dedup.py:24-43` | ~1,000 µs | ~96% |
| Pattern scan, P=100 | `bus.py:85-86` | ~20 µs | ~1.9% |
| `asyncio.wait_for` wrapper | `bus.py:62` | ~7 µs | ~0.7% |
| `json.dumps` for the log line | `logging.py:30` | ~5 µs | ~0.5% |
| `Event()` construction | `event.py:15-16` | ~3 µs | ~0.3% |
| Retry dict insert | `retry.py:25` | ~0.1 µs | <0.1% |
| **Total** | | **~1,035 µs** | |

At 1,035 µs for each event, one core carries about 970 events per second. This is below assumption A1. The queue then fills, and `publish` blocks at `bus.py:46`. Every publisher pays that delay.

## Findings

### [P1] `DeduplicationMiddleware._cleanup_expired` scans all 10,000 entries for each event

- **Category**: Algorithms
- **Location**: `eventbus/middleware/dedup.py:16`, `eventbus/middleware/dedup.py:24-33`, `eventbus/middleware/dedup.py:38-43`
- **Hot path**: `EventBus._process_loop` awaits each middleware for each event at `eventbus/bus.py:70`. `DeduplicationMiddleware.__call__` at `dedup.py:14` calls `_cleanup_expired()` on line 16. The call happens for every event, and it happens before the duplicate test on line 18. A duplicate event that the middleware then filters also pays the full scan.
- **Problem**: The method finds expired entries with a full dictionary scan. The dictionary `_seen` holds up to `max_seen=10000` entries (`dedup.py:9`). The scan cost grows with the number of tracked entries, not with the number of expired entries. At steady state with `window_seconds=60.0`, almost no entry expires on a given pass, so the scan does 10,000 tests to delete 0 keys. The code has no fast path for an empty dictionary and no fast path for the common case of 0 expired entries. `_seen` is already insertion-ordered, so the oldest entry always sits at the front. The scan reads the whole dictionary to find data that is available at the front.

  Line 42 adds a second full pass. `list(self._seen.keys())` copies all 10,000 keys into a new list to take the first `excess` keys. This list is 80 KB, and the code allocates it for each event once `_seen` reaches the cap.
- **Evidence**:
  ```python
  expired = [k for k, ts in self._seen.items()
             if now - ts > self.window_seconds]
  for k in expired:
      del self._seen[k]
  ...
  for k in list(self._seen.keys())[:excess]:
      del self._seen[k]
  ```
- **Estimate**: The comprehension does about 4 operations for each entry: one item unpack, one subtraction, one comparison, and the loop step. At 20 ns for each operation (A5), one entry costs about 80 ns. 10,000 entries × 80 ns = **800 µs for each event**. A wider range of 40–150 ns for each entry gives 400 µs–1.5 ms. The key copy on line 42 adds 10,000 × ~25 ns = **250 µs for each event** once `_seen` is at the cap. The total is about **1.05 ms for each event**.

  At 1,000 events per second (A1), the method uses 1.05 s of CPU time for each second of events. One core cannot keep up. The scan alone caps the bus at about **950 events per second**.

  A front-pop version tests only the oldest entry and stops. With 0 expired entries it costs about **1 µs**. The saving on this step is about **1,000x**. The per-event budget drops from ~1,035 µs to ~35 µs, so the estimated ceiling rises from ~970 to **~28,000 events per second**, about **29x**.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Keep a `deque` of `(event_id, timestamp)` pairs beside the `_seen` dictionary. Pop from the left while the oldest timestamp is expired. Pop from the left also while `len(_seen) > max_seen`. Both loops then cost O(k), where k is the number of entries that the code really removes. Return early when `_seen` is empty. Pass the `now` value that line 21 already needs, so `time.time()` runs one time for each event. Keep the existing `window_seconds` and `max_seen` semantics. Measure the CPU time of `__call__` for each event at `_seen` sizes of 100, 1,000, and 10,000. The fixed version must show a flat cost across all three sizes.

### [P1] `SQLiteStorage.store` commits one transaction for each event

- **Category**: I/O and Round Trips
- **Location**: `eventbus/storage/sqlite.py:39-45`
- **Hot path**: No caller inside this repository calls `SQLiteStorage.store()`. `_process_loop` at `bus.py:58-95` never uses storage. The multiplier is the per-event call from a library user. Three facts support the per-event contract: `store()` takes exactly one `Event`; the sibling method `MemoryStorage.store()` at `eventbus/storage/memory.py:16` has the same signature and the same per-event shape; and `pyproject.toml:4` names persistent storage as a package feature. Read this finding as conditional on that per-event call.
- **Problem**: The method runs one `INSERT` and then one `commit()` for each event. A commit forces an fsync to disk. The fsync, not the insert, sets the throughput limit. A caller that persists a stream of events pays one disk write for each event. `initialize()` at `sqlite.py:16-31` also leaves SQLite in the default rollback journal mode, which costs more disk writes for each commit than WAL mode does.
- **Evidence**:
  ```python
  await self._db.execute(
      "INSERT OR REPLACE INTO events (event_id, topic, payload, timestamp, source) "
      "VALUES (?, ?, ?, ?, ?)",
      (event.event_id, event.topic, json.dumps(event.payload),
       event.timestamp, event.source)
  )
  await self._db.commit()  # BUG [PERFORMANCE]: Commits after every single insert
  ```
- **Estimate**: One commit costs one disk write, about 1–10 ms. Take 5 ms. At 1,000 events per second (A1), the commits need 1,000 × 5 ms = **5 s of disk time for each second of events**. The write path cannot keep up. The per-event commit caps throughput at 1/0.005 = **200 events per second**.

  One transaction that holds 100 inserts needs 1 commit, not 100. The fsync count drops **100x**. At 1,000 events per second, the disk time drops from 5 s to 10 commits × 5 ms = **50 ms for each second of events**, about 5% duty. WAL mode typically cuts the remaining write cost by a further 2x–3x for this insert-only pattern.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a `store_many(events)` method that runs `executemany` inside one transaction and commits one time. Keep `store()` for the single-event case. Add `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` to `initialize()` at `sqlite.py:16`; this is a one-line change in cold code that cuts the hot write cost. Note the trade-off: a batch loses the events that are not yet committed if the process stops, and `synchronous=NORMAL` can lose the last transaction after an operating system crash. Measure the commit count and the events per second for a run of 10,000 events, at batch sizes of 1, 10, and 100.

### [P1] `RetryMiddleware` writes a dictionary entry for each event and never reads it

- **Category**: Observability Overhead
- **Location**: `eventbus/middleware/retry.py:20`, `eventbus/middleware/retry.py:25`
- **Hot path**: `_process_loop` awaits each middleware for each event at `bus.py:70`. `RetryMiddleware.__call__` at `retry.py:22` adds one entry to `_retry_counts` for each event.
- **Problem**: A grep across the whole package shows `_retry_counts` at exactly two places: the constructor on line 20 and the write on line 25. No code reads it. No code deletes an entry from it. The dictionary grows by one entry for each event and never shrinks, so the process runs out of memory in the end.

  `with_retry` at `retry.py:28` does hold correct exponential backoff logic, but it uses a local `attempt` counter and never touches `_retry_counts`. No code calls `with_retry` either. The bus dispatch at `bus.py:89` calls the handler directly. So the registered middleware performs the unbounded write and nothing else.
- **Evidence**:
  ```python
  async def __call__(self, event: Event) -> Event:
      """Pass through — retry logic is applied at handler dispatch time."""
      # Track that this event entered the retry middleware
      self._retry_counts[event.event_id] = 0
      return event
  ```
- **Estimate**: One entry holds a 36-character `event_id` string (about 85 bytes) and a cached small integer (0 bytes). The dictionary slot and index table add about 40 bytes. One entry costs about **130 bytes**. At 1,000 events per second (A1): 130 bytes × 1,000 × 3,600 = **about 470 MB for each hour**, with no bound. A process that runs for one day holds about 11 GB.

  The CPU cost is small, about 0.1 µs for each event. The memory growth is the real harm. The saving is 100% of both costs, because the fix deletes dead data and changes no behavior.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete line 20 and line 25. Keep `with_retry`, and wire it into the dispatch loop at `bus.py:89` if the retry behavior is wanted; that is a separate change. If a retry count is needed later, key it inside `with_retry` and delete the entry when the handler succeeds. Measure the resident memory of the process across 1,000,000 events. It must stay flat.

### [P1] `MemoryStorage._by_topic` retains every event and defeats the `maxlen` bound

- **Category**: Allocation and Copies
- **Location**: `eventbus/storage/memory.py:13`, `eventbus/storage/memory.py:19-21`
- **Hot path**: `MemoryStorage.store()` at `memory.py:16` runs one time for each event that a user persists. As with the SQLite finding, no caller inside this repository calls it, so read this finding as conditional on the per-event call that the method signature defines.
- **Problem**: The class holds each event in two structures. `_events` is a `deque` with `maxlen=max_events` (default 100,000), so it evicts the oldest event and frees it. `_by_topic` is a dictionary of plain lists with no bound. The lists only grow. Only `clear()` at `memory.py:36` ever trims them.

  So the `maxlen` on line 13 does not bound the memory of the class. When the deque evicts an event, `_by_topic` still holds a reference to it, and Python cannot free it. The class retains every event that it has ever stored. The file header comment on line 8 states that the class is clean, so a reader can miss this.
- **Evidence**:
  ```python
  self._events: deque[Event] = deque(maxlen=max_events)
  self._by_topic: dict[str, list[Event]] = {}
  ...
  self._events.append(event)
  if event.topic not in self._by_topic:
      self._by_topic[event.topic] = []
  self._by_topic[event.topic].append(event)
  ```
- **Estimate**: One `Event` holds an instance dictionary (about 150–230 bytes), a topic string (about 55 bytes), a 36-character `event_id` string (about 85 bytes), a payload dictionary of 10 keys (about 600 bytes at A4), and a source string (about 50 bytes). One event costs about **1,000 bytes**. The list slot adds 8 bytes.

  At 1,000 events per second (A1) and above the 100,000-event cap: 1,000 bytes × 1,000 × 3,600 = **about 3.6 GB for each hour**, with no bound. The deque cap suggests a memory ceiling of about 100 MB. The real usage passes that ceiling after 100 seconds and never stops.

  The fix holds the memory at the bound that the author already chose: about 100,000 events, or about 100 MB.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Make each per-topic container a `deque(maxlen=max_events)` as well. This matches the bound on line 13 and keeps `get_by_topic` correct, because line 26 already returns only the last `limit` items. Delete a topic key when its deque becomes empty, so `_by_topic` does not keep dead topic keys. Note the trade-off: a per-topic cap of `max_events` still lets the total across T topics reach T × `max_events`, so add a global check if a hard ceiling is needed. Measure the resident memory across 1,000,000 stored events with `max_events=100000`. It must level off.

### [P1] `WebhookHandler` runs a blocking HTTP call inside an async handler

- **Category**: I/O and Round Trips
- **Location**: `eventbus/handlers/webhook.py:43`, `eventbus/handlers/webhook.py:19`
- **Hot path**: `_process_loop` awaits each matching handler in turn at `bus.py:89`. `WebhookHandler.__call__` at `webhook.py:24` is such a handler. `urllib.request.urlopen` on line 43 is synchronous, so it blocks the thread that runs the event loop.
- **Problem**: The blocked thread is the only thread of the event loop. During the POST, no other coroutine in the process runs. This stalls every publisher at `bus.py:46`, every other handler, and every timer. The stall is not limited to the bus; it hits all async work in the process.

  The default timeout is 30 seconds (`webhook.py:19`). A slow endpoint or a hung endpoint therefore freezes the whole process for up to 30 seconds. `urllib` also opens a new TCP connection and a new TLS session for each call and pools nothing, so each event pays a fresh handshake.
- **Evidence**:
  ```python
  # BUG [PERFORMANCE]: Uses synchronous urllib in async handler — blocks the event loop.
  # Should use aiohttp or httpx for async HTTP.
  try:
      response = urllib.request.urlopen(req, timeout=self.timeout)
  ```
- **Estimate**: A POST to an external webhook costs about 50 ms with a warm path, and 150 ms or more across a continent. Take 50 ms. The bus then handles 1/0.050 = **20 events per second**, against the 1,000 per second of A1. The queue of 10,000 (`bus.py:22`) fills in 10,000/20 = **500 seconds**, and `publish` blocks after that.

  A new TLS handshake costs 2 extra round trips. On a California-to-Netherlands path at 150 ms for each round trip, that is **300 ms of the 500 ms call**. Connection reuse removes it.

  An async client with a shared connection pool lets N requests overlap. At a pool of 20 and 50 ms for each call, throughput reaches about 400 events per second, about **20x**. The event loop then stays free for all other work, which is the larger win.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Replace `urllib` with `httpx.AsyncClient` or `aiohttp`. Build one client for each handler instance in `__init__` and reuse it, so the pool keeps connections warm. Add an `aclose()` method and call it when the bus stops. Cut the default timeout from 30 s to about 5 s; a 30 s timeout on a serving path only extends the stall. As a smaller first step, wrap the current call in `asyncio.to_thread()`; this frees the event loop but keeps the per-call connection cost. Measure the event loop lag with `loop.slow_callback_duration` and a debug run. It must drop from tens of milliseconds to under 1 ms.

### [P1] The dispatch loop awaits every handler in sequence, and one task processes all events

- **Category**: I/O and Round Trips
- **Location**: `eventbus/bus.py:85-92`, `eventbus/bus.py:51`
- **Hot path**: This is the dispatch stage of `_process_loop` at `bus.py:58`, which runs for every event. `bus.py:51` creates exactly one `_process_loop` task, so the bus holds one event in flight at a time.
- **Problem**: The loop has two levels of serial work. Line 87 awaits each handler of a matching pattern one after another. Line 85 walks the patterns one after another as well. So the latency of one event is the sum of the latency of all its handlers. Independent handlers do not overlap.

  The single consumer task adds a second limit. Even with fast handlers, event N+1 waits for event N to finish all its handlers. The queue of 10,000 (`bus.py:22`) buffers the burst but does not raise the drain rate.

  This finding applies only when handlers do I/O. With in-process handlers of about 10 µs, `asyncio.gather` adds more overhead than it saves.
- **Evidence**:
  ```python
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
          for handler in handlers:
              try:
                  await handler(processed_event)
                  dispatched = True
  ```
- **Estimate**: Assume 3 matching handlers, and 50 ms of I/O for each one. The current code costs 3 × 50 ms = **150 ms for each event**, so the bus drains 1/0.150 = **6.7 events per second**. Concurrent handlers cost about 50 ms for each event, so the bus drains about **20 events per second**, a **3x** gain. With 10 matching handlers the gain is **10x**.

  A pool of W consumer tasks multiplies the drain rate by a further W for I/O-bound handlers. At W=10 and concurrent handlers, the estimate reaches about 200 events per second, against 6.7 now, about **30x**.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Collect the matching handlers first, then run them with `asyncio.gather(*calls, return_exceptions=True)`. `return_exceptions=True` keeps today's behavior, where one handler failure does not stop the others. Then start W `_process_loop` tasks in `start()` at `bus.py:48` and make W a constructor option with a default of 1. State the trade-off in the docstring: concurrent dispatch drops the current ordering guarantee, because handlers of one event no longer run in registration order, and events no longer complete in publish order. Keep W=1 as the default so existing users keep the ordered behavior. Measure the events per second and the p99 latency for each event with 3 handlers of 50 ms each, at W=1 and W=10.

### [P2] `EventBus._dead_letter` grows without a bound, and only its length is read

- **Category**: Allocation and Copies
- **Location**: `eventbus/bus.py:24`, `eventbus/bus.py:94-95`, `eventbus/bus.py:104`
- **Hot path**: `_process_loop` appends to the list at `bus.py:95` for every event that no handler takes. `status()` at `bus.py:104` reads `len(self._dead_letter)` and nothing else.
- **Problem**: The list keeps every dead-lettered event forever. No code removes an entry, and no code reads an entry. The only reader wants a count, so the events sit in memory to support a number that a counter can hold in 28 bytes.

  A second effect makes the growth worse. Line 90 sets `dispatched = True` only after a handler returns without an exception. If every matching handler raises, the flag stays `False`, and line 95 appends the event even though a pattern matched it. So a handler that fails for every event sends every event to the unbounded list.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []  # BUG [PERFORMANCE]: Unbounded list grows forever
  ...
  if not dispatched:
      self._dead_letter.append(processed_event)
  ```
- **Estimate**: One retained `Event` costs about 1,000 bytes at A4, as computed in the `_by_topic` finding. A correct configuration leaves few events unmatched, so take 100 unmatched events per second, that is 10% of A1. Then 1,000 bytes × 100 × 3,600 = **about 360 MB for each hour**, with no bound.

  With the failing-handler case above, the rate reaches the full 1,000 events per second, and the growth reaches **about 3.6 GB for each hour**.

  A `deque(maxlen=1000)` holds about 1 MB and keeps the recent events for inspection. A separate integer counter keeps the `status()` number exact. The memory saving is the entire unbounded part.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change `_dead_letter` to `collections.deque(maxlen=1000)` and add a `_dead_letter_total` integer that only increases. Report the integer in `status()` on line 104, so the count stays correct after the deque evicts. Make `maxlen` a constructor option. Set `dispatched = True` before the `try` block on line 88, so a handler error no longer marks a matched event as dead-lettered; the error already reaches the log on line 92. Note that this changes the count that `tests/test_bus.py:52` asserts only for the failing-handler case, not for the unmatched-topic case that the test covers. Measure the resident memory across 1,000,000 unmatched events. It must stay flat.

### [P2] `LoggingMiddleware` builds the JSON string even when the log level is off

- **Category**: Observability Overhead
- **Location**: `eventbus/middleware/logging.py:18-30`
- **Hot path**: `_process_loop` awaits each middleware for each event at `bus.py:70`. `LoggingMiddleware.__call__` at `logging.py:16` runs for every event.
- **Problem**: The method builds a 4-key dictionary, adds the payload, and calls `json.dumps` on line 30. It passes the finished string to `logger.log`. If the logger is not enabled for `self.level`, `logger.log` discards that string. The default level is `logging.INFO` (`logging.py:12`), and the default root level in Python is `WARNING`, so the default setup discards the string. The code pays the full cost and gets no log line.

  The method has no `isEnabledFor` check. It also serializes the payload here, and `SQLiteStorage.store` at `sqlite.py:42` serializes the same payload again for the same event.
- **Evidence**:
  ```python
  log_data = {
      "event_id": event.event_id,
      ...
  }
  if self.include_payload:
      log_data["payload"] = event.payload

  logger.log(self.level, json.dumps(log_data))
  ```
- **Estimate**: `json.dumps` on a dictionary of 14 keys, with a 10-key nested payload (A4), costs about **5 µs**. The dictionary build adds about 0.5 µs. The total is about **5.5 µs for each event**.

  At 1,000 events per second (A1): 5.5 ms of CPU time for each second, about **0.5% of one core**. At 10,000 events per second: about 5% of one core. When the level is off, 100% of that cost is waste.

  An `isEnabledFor` check costs about 0.1 µs. The saving on this step is about **50x** when the level is off. The saving is 0 when the level is on, because the serialization is then real work.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `if not logger.isEnabledFor(self.level): return event` as the first line of `__call__`. Then move the dictionary build and the `json.dumps` call below it. Measure the CPU time for each event with the root logger at `WARNING`. It must drop to near 0 for this middleware.

### [P2] The dispatch loop tests every registered pattern for each event, and `unsubscribe` leaves dead keys

- **Category**: Algorithms
- **Location**: `eventbus/bus.py:85-86`, `eventbus/bus.py:34`, `eventbus/event.py:19-26`
- **Hot path**: `_process_loop` walks all of `self._handlers` at `bus.py:85` and calls `processed_event.matches(pattern)` at `bus.py:86` for every event.
- **Problem**: The scan is O(P), where P is the number of registered patterns. Most patterns are exact topic names, and an exact name needs one dictionary probe, not a linear scan. `matches` at `event.py:19` is a Python method call for each pattern. For a `.*` pattern it also slices the pattern on line 24 and builds a new string with `prefix + "."` on line 25; it allocates that string again for each pattern and for each event, although the prefix is fixed when the user subscribes.

  `unsubscribe` at `bus.py:34` makes P grow without a bound. It removes the handler from the list but never removes the key. `self._handlers` keeps a `pattern -> []` entry forever. So the per-event scan cost grows with the total number of subscriptions in the history of the process, not with the number of live subscriptions. A service that subscribes and unsubscribes per request pays more for each event as it runs longer.
- **Evidence**:
  ```python
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
  ```
  ```python
  def unsubscribe():
      self._handlers[pattern].remove(handler)
  ```
- **Estimate**: One `matches` call on an exact pattern costs a Python call (about 60 ns), a comparison with `"*"`, an `endswith` method call (about 50 ns), and a final comparison. That is about **200 ns for each pattern**. At P=100 (A3): 100 × 200 ns = **20 µs for each event**. At 1,000 events per second, that is 20 ms for each second, about **2% of one core**.

  A wildcard pattern costs more, about 400 ns, because of the slice and the string build. At P=100 wildcards the cost is about 40 µs for each event.

  An exact-match dictionary probe costs about 0.05 µs. With 100 exact patterns and 5 wildcard patterns, the scan drops from 20 µs to about 0.05 + 5 × 0.4 = **2 µs for each event**, about **10x** on this step.

  The scan grows to 200 µs for each event at P=1,000, which is 20% of one core at 1,000 events per second. With subscription churn, P has no ceiling, so this cost has no ceiling.
- **Impact**: Medium
- **Effort**: Medium (hours)
- **Suggested fix**: Take the two steps in this order. First, in `unsubscribe` at `bus.py:34`, delete the key when the list becomes empty; this is 2 lines and it removes the unbounded growth. Second, split `_handlers` into an exact-match dictionary, keyed by topic, and a small list of `(prefix, handlers)` pairs for the `.*` patterns, plus one list for the `"*"` pattern. Compute each prefix one time at subscribe time, so `matches` no longer builds `prefix + "."` for each event. The dispatch then does one dictionary probe plus one pass over the wildcard list. Keep the returned unsubscribe function working against the new structure. Measure the dispatch CPU time for each event at P=10, P=100, and P=1,000 exact patterns; the fixed version must stay flat.

### [P3] `asyncio.wait_for` wraps the queue read on every loop pass

- **Category**: Redundant Work
- **Location**: `eventbus/bus.py:62`
- **Hot path**: `_process_loop` runs line 62 one time for each event, and also 10 times per second while the queue is empty.
- **Problem**: `asyncio.wait_for` wraps the `get()` coroutine in a Task, schedules a timer with `call_later`, and then cancels that timer. The loop pays this setup and teardown for each event. A bare `await self._queue.get()` needs none of it. The 0.1 s timeout exists so that a change of `_running` to `False` in `stop()` at `bus.py:55` takes effect within 100 ms. A sentinel value in the queue, or `task.cancel()`, gives a faster and cheaper shutdown.
- **Evidence**:
  ```python
  event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
  ```
- **Estimate**: Task creation costs about 2–5 µs. The timer schedule and cancel cost about 2–5 µs. The extra future callbacks cost about 1–2 µs. The total is about **5–12 µs for each event**, against about 1 µs for a bare `get()`. Take **7 µs of waste for each event**.

  At 1,000 events per second (A1): 7 ms for each second, about **0.7% of one core**. This is 0.7% of the per-event budget after the dedup fix, and it is under 0.02% when a handler does 50 ms of I/O. The saving is real but small.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace line 62 with `event = await self._queue.get()`. Change `stop()` at `bus.py:53` to put a sentinel object into the queue, and break out of the loop when the sentinel arrives. This also fixes the shutdown latency, which is now up to 100 ms. Do this change when you next touch `_process_loop`, and prefer to do it together with the queue drain that `stop()` needs anyway. Measure the CPU time for each event of the queue read step.

### [P3] `Event` does not use `slots=True`

- **Category**: Data Layout
- **Location**: `eventbus/event.py:10-17`
- **Hot path**: `Event` is the highest-count object in the system. `create_event` at `event.py:30` and every direct call build one for each event. `matches` at `event.py:25` loads `self.topic` one time for each pattern and for each event, so at P=100 that is 100 attribute loads for each event.
- **Problem**: The dataclass has no `slots=True`, so each instance carries an instance dictionary. Two structures retain these instances at the same time: `MemoryStorage._events` and `MemoryStorage._by_topic` (`memory.py:13`). Attribute access through a dictionary is also slower than access through a slot.
- **Evidence**:
  ```python
  @dataclass(frozen=True)
  class Event:
      """An immutable event with metadata."""
      topic: str
  ```
- **Estimate**: An instance with a dictionary costs about 48 bytes for the object plus about 104–184 bytes for the dictionary, that is about **150–230 bytes**. With `slots=True` it costs about 40 + 5 × 8 = **80 bytes**. The saving is about **100 bytes for each instance**. At the 100,000-event cap of `MemoryStorage` (`memory.py:12`), that is about **10 MB**.

  Slot access is about 20–30% faster than dictionary access. At P=100 (A3) and 100 attribute loads for each event, this saves about 100 × 10 ns = **1 µs for each event**, about 0.1% of one core at 1,000 events per second.

  `frozen=True` and `slots=True` work together in Python 3.10 and later. `pyproject.toml:5` requires Python 3.11, so the change is available.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change line 10 to `@dataclass(frozen=True, slots=True)`. Check first that no caller sets an extra attribute on an `Event` and that no caller takes a weak reference to one; no code in this repository does either. Measure `sys.getsizeof` on an instance and the resident memory of `MemoryStorage` at 100,000 events.

### [P3] `MemoryStorage.get_recent` copies all 100,000 events to return 100

- **Category**: Allocation and Copies
- **Location**: `eventbus/storage/memory.py:28-30`
- **Hot path**: This is a read method, not a per-event method. A dashboard or a poller calls it. The multiplier is the poll rate, which this repository does not set. The finding assumes a poll of 1 call per second.
- **Problem**: `list(self._events)` builds a new list of every event in the deque, then the slice takes the last 100 and discards the rest. The deque holds up to `max_events=100000` (`memory.py:12`). So the method copies 100,000 pointers and increments 100,000 reference counts to return 100 items. A deque supports an iterator slice, which touches only the last 100.
- **Evidence**:
  ```python
  async def get_recent(self, limit: int = 100) -> list[Event]:
      """Get most recent events across all topics."""
      return list(self._events)[-limit:]
  ```
- **Estimate**: The list build allocates 100,000 × 8 bytes = **800 KB** and does 100,000 reference count increments at about 10–20 ns each. The total is about **1–3 ms for each call**. `itertools.islice(self._events, len(self._events) - limit, None)` touches 100 items and costs about **5 µs**, that is about **300x** less.

  At 1 poll per second, the saving is 1–3 ms for each second, about **0.2% of one core**. The 800 KB allocation for each call also adds pressure to the garbage collector. The method is a good place for a cheap fix, but the total saving is small at this call rate. Raise this priority if the real poll rate is much higher than 1 per second.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Return `list(itertools.islice(self._events, max(0, len(self._events) - limit), None))`. The `max(0, ...)` guard keeps the method correct when the deque holds fewer than `limit` events. Compare the result against the current output for deque sizes of 0, 50, and 100,000. Measure the time for each call at a deque size of 100,000.

## Cold-Code Notes

- `eventbus/storage/sqlite.py:19-31` — `initialize()` runs the `CREATE TABLE` and `CREATE INDEX` statements at startup. This is one-shot code. It does set the journal mode that the hot write path pays for, so the P1 SQLite finding covers the pragma change.
- `eventbus/storage/sqlite.py:69` — `count()` runs `SELECT COUNT(*) FROM events`, a full scan of the table. On a table of 10,000,000 rows this reads the whole primary key index, about 1 s. This is an admin or status call. Raise it to a finding only if a dashboard polls it.
- `eventbus/bus.py:100-105` — `status()` builds a new dictionary over all patterns for each call. This is an admin call, not a per-event call. The cost is about 100 × 100 ns = 10 µs at P=100.
- `tests/test_bus.py:18`, `tests/test_bus.py:38`, `tests/test_bus.py:49` — each test waits with a fixed `await asyncio.sleep(0.2)`. The three sleeps add 0.6 s, which is most of the wall time of the suite. The absolute time is too small to block CI, so this stays a note. An `asyncio.Event` that the handler sets would remove the fixed wait and also remove the flake risk on a loaded machine.
- `eventbus/handlers/webhook.py:52-57` — `create_webhook_handler` builds a headers dictionary for each call. It is a factory that runs at setup time, one time for each handler.

## Not Found

- **Algorithms**: No O(N²) nested loop over the same collection exists. No membership test against a list inside a loop exists. No full sort serves a minimum, a maximum, or a top-k query. No string concatenation grows inside a loop. The two algorithmic problems that this report does raise are the O(N) dedup scan (P1) and the O(P) dispatch scan (P2).
- **Data Layout**: The `_handlers` dictionary uses topic pattern strings as keys. These are not small dense integers, so an array or a bit vector does not apply. The `Event.payload` field is a generic dictionary, which the public API requires. The one layout finding is the missing `slots=True` (P3).
- **Redundant Work**: No regex compilation happens inside a loop; the code uses `str.startswith` and `str.endswith` instead of regexes, which is the faster choice already. No date parsing or config read happens for each event.
- Dismissed after a check, with the number that dismissed it:
  - `uuid.uuid4()` for each event at `event.py:15` costs about 2 µs, which is 0.2% of one core at 1,000 events per second. An event needs a unique identity, so this work is real. `uuid4().hex` would save about 0.2 µs and change the identity format. Not worth it.
  - `frozen=True` at `event.py:10` adds about 0.75 µs for each event, because `__init__` calls `object.__setattr__` for each of the 5 fields. That is 0.07% of one core at 1,000 events per second, and it buys immutability, which is a correctness property. Keep it.
  - `get_by_topic` at `memory.py:26` slices with `events[-limit:]`, which copies only `limit`=100 pointers, about 2 µs. This is correct and cheap. Only the unbounded growth of the underlying list is a problem, and the P1 finding covers that.
  - `SQLiteStorage.get_by_topic` at `sqlite.py:54-58` uses a parameterized query with `ORDER BY timestamp DESC LIMIT ?` and an index on `topic` (`sqlite.py:29`). The query plan is sound. The comment on lines 51-53 raises SQL injection and then withdraws it; the code is correct, and the point is not a performance point either.
  - `defaultdict(list)` at `bus.py:20` is the right structure for the subscribe path. Only the missing key deletion in `unsubscribe` is a problem, and the P2 finding covers that.
  - `RetryMiddleware.with_retry` at `retry.py:31-41` uses correct exponential backoff, `base_delay * (2 ** attempt)`. The delays of 1 s, 2 s, and 4 s are appropriate, and the method is not on a hot path. No change needed. Note that no code calls it, which the P1 retry finding states.