# Performance Audit Report

## Summary

The `eventbus` package is an in-process async event bus for Python 3.11 or later. Events enter through `EventBus.publish` (bus.py:42), wait in one `asyncio.Queue` with a default limit of 10,000 slots (bus.py:22), and leave through one consumer coroutine, `EventBus._process_loop` (bus.py:58). That loop is the single hot path: it runs the middleware chain (bus.py:68) and it awaits every handler (bus.py:89) one event at a time. The single best quick win is a one-line delete: `RetryMiddleware` writes an entry into `self._retry_counts` for every event (retry.py:25), no code reads that dict, and the code never removes an entry. That one line adds about 2 GB of live memory per day at the design event rate.

The authors already mark five of these defects with `# BUG [PERFORMANCE]` comments. This report confirms them, supplies the arithmetic, adds the defects that the comments do not mark, and ranks all of them by impact against effort.

Shared assumptions for every Estimate below. These are estimates, not measurements:
- The design event rate is about 167 events per second. `DeduplicationMiddleware` defaults to `max_seen: int = 10000` and `window_seconds: float = 60.0` (dedup.py:9). 10,000 events / 60 s = 167 events per second.
- One `Event` with a small payload uses about 600 bytes: the frozen dataclass instance about 56 bytes, the payload dict about 184 bytes, the 36-character uuid string about 85 bytes, plus the topic string and the payload contents.
- One Python statement costs 10 ns to 100 ns.
- One webhook HTTP round trip takes about 100 ms.
- One SQLite commit costs about 5 ms.

## Findings

### [P1] The webhook handler uses synchronous HTTP inside the async consumer loop

- **Category**: I/O and Round Trips
- **Location**: eventbus/handlers/webhook.py:43, eventbus/handlers/webhook.py:19
- **Hot path**: `EventBus._process_loop` awaits each handler at bus.py:89. `EventBus.start` creates one consumer task at bus.py:51, so one call to `urlopen` stops the whole loop.
- **Problem**: `urllib.request.urlopen` is synchronous. It holds the thread that runs the event loop for the full HTTP round trip. Nothing else runs during that time: no other handler, no other event, and no other coroutine in the host process. The bus is in-process, so the application that embeds the bus also stalls. `urlopen` also opens a new TCP connection for each call, because it keeps no connection pool.
- **Evidence**:
  ```python
  # BUG [PERFORMANCE]: Uses synchronous urllib in async handler — blocks the event loop.
  # Should use aiohttp or httpx for async HTTP.
  try:
      response = urllib.request.urlopen(req, timeout=self.timeout)
  ```
- **Estimate**: This is an estimate. One webhook round trip takes about 100 ms. The loop processes events one at a time, so the bus caps at 1 / 0.1 s = 10 events per second. The design rate is 167 events per second, so the cap is about 16 times too low. A new TLS handshake adds 2 more round trips for each event. Across a datacenter that adds about 1 ms to 3 ms. Between California and the Netherlands it adds about 300 ms. The default `timeout: int = 30` (webhook.py:19) sets the worst case: one hung endpoint stops the whole process for 30 s. At 167 events per second the 10,000-slot queue fills in 60 s, and then `publish` blocks every producer.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the call in `await asyncio.to_thread(urllib.request.urlopen, req, timeout=self.timeout)`. This frees the event loop for the length of the round trip. For the full fix, replace `urllib` with an `httpx.AsyncClient` that the handler holds on the instance. The client keeps a connection pool, so it removes the per-event TLS handshake. Measure events per second through the bus with one webhook subscriber. Measure the maximum delay of the event loop with `asyncio` debug mode before and after.

### [P1] The deduplication middleware scans all 10,000 seen ids on every event

- **Category**: Algorithms
- **Location**: eventbus/middleware/dedup.py:16, eventbus/middleware/dedup.py:24-43
- **Hot path**: `EventBus._process_loop` runs the middleware chain at bus.py:68-70, once for every event. `DeduplicationMiddleware.__call__` calls `self._cleanup_expired()` on its first line (dedup.py:16), before the duplicate test at dedup.py:18. A duplicate event pays the full scan too.
- **Problem**: `_cleanup_expired` builds a list comprehension over every entry in `self._seen` to find the expired ones. The default limit is `max_seen: int = 10000` (dedup.py:9). The work is O(N) for each event, and N reaches 10,000. The dict is already in insertion order, which is timestamp order, so the code can expire from the front in O(1) amortized time. A second block at dedup.py:42 copies every key into a new list only to take the first few keys.
- **Evidence**:
  ```python
  expired = [k for k, ts in self._seen.items()
             if now - ts > self.window_seconds]
  ```
  ```python
  for k in list(self._seen.keys())[:excess]:
  ```
- **Estimate**: This is an estimate. Each pass of the comprehension does a tuple unpack, a subtraction, a comparison, and a loop step: about 80 ns. 10,000 entries × 80 ns = 800 µs for each event. The `list(self._seen.keys())` copy adds 10,000 pointer copies × about 15 ns = 150 µs, plus about 80 KB of garbage. In a steady state above the limit, that block runs for every event. The total is about 0.8 ms to 0.95 ms of CPU for each event on the single consumer coroutine. 800 µs equals 16 round trips in the same datacenter. At 167 events per second the cost is 133 ms per second, which is 13% of one core. At 1,000 events per second the cost is 800 ms per second, which is 80% of one core.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Expire the entries from the front instead of a full scan. Keep the ids in a `collections.OrderedDict`, or keep a `deque` of `(event_id, timestamp)` pairs beside the dict. Pop from the front while the front entry is older than the window. Stop at the first entry inside the window. Use the same front-pop loop for the `max_seen` limit. Also read `time.time()` into one local variable instead of the two calls at dedup.py:21 and dedup.py:26. The expected saving is from about 800 µs to under 1 µs for each event, which is about 800 times less CPU on this step. Measure the CPU time of `_cleanup_expired` with `cProfile` at a fixed event rate.

### [P1] SQLite storage commits after every single event

- **Category**: I/O and Round Trips
- **Location**: eventbus/storage/sqlite.py:45, eventbus/storage/sqlite.py:19-31
- **Hot path**: No caller of `store` exists inside this package. The hotness comes from the shape of the API and from the purpose of the package. `SQLiteStorage.store` accepts one `Event` (sqlite.py:33), and the package description is "In-process event bus with persistent storage and middleware support" (pyproject.toml:4). Any caller that persists events runs one `store` call for each event, from the consumer loop at bus.py:58. Treat the call rate as an assumption, not as a measured fact.
- **Problem**: `store` runs one `INSERT` and then one `commit`. A commit forces an fsync to the disk. `initialize` does not set a journal mode (sqlite.py:19-31), so the database uses the default rollback journal. That mode does several fsyncs for each commit: it writes the journal, it syncs the journal, it writes the page, and it deletes the journal. The fsync cost is far larger than the cost of the insert that it protects.
- **Evidence**:
  ```python
  await self._db.commit()  # BUG [PERFORMANCE]: Commits after every single insert
  ```
- **Estimate**: This is an estimate. One commit costs about 5 ms, in the range of 1 ms to 10 ms for one disk write. One fsync for each event caps write throughput at 1 / 0.005 s = 200 events per second. The rollback journal does 2 or 3 fsyncs for each commit, so the real cap is about 50 to 150 events per second. The design rate is 167 events per second, so the storage is the bottleneck. The `INSERT` itself costs about 10 µs to 50 µs, so the commit is 100 to 500 times the cost of the useful work. One commit for each batch of 100 events gives 5 ms / 100 = 50 µs for each event, which cuts the commit cost by about 100 times.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a `store_many(events)` method. Run one `executemany` and then one `commit` for the whole batch. Keep `store` as a wrapper that calls `store_many` with one event, so existing callers still work. Add `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` to `initialize`. WAL removes the extra journal fsyncs and it lets a reader run during a write. Measure events per second into the database. Count the fsync calls with `dtruss` on macOS or `strace -c` on Linux.

### [P1] The retry middleware fills a dict that no code reads and never frees

- **Category**: Observability Overhead
- **Location**: eventbus/middleware/retry.py:20, eventbus/middleware/retry.py:25
- **Hot path**: `EventBus._process_loop` runs the middleware chain at bus.py:68-70, once for every event. `RetryMiddleware.__call__` writes one dict entry for every event that passes through.
- **Problem**: `self._retry_counts[event.event_id] = 0` stores a counter for every event. A search of the whole package finds only the assignment at retry.py:25 and the declaration at retry.py:20. No code reads the dict. `with_retry` (retry.py:28-41) counts its attempts with the local loop variable `attempt` and never touches `self._retry_counts`. The code also never deletes an entry, so the dict grows for the life of the process. A Python dict never returns memory to the allocator after it grows.
- **Evidence**:
  ```python
  async def __call__(self, event: Event) -> Event:
      """Pass through — retry logic is applied at handler dispatch time."""
      # Track that this event entered the retry middleware
      self._retry_counts[event.event_id] = 0
      return event
  ```
- **Estimate**: This is an estimate. One dict entry holds a 36-character uuid string. The string object uses about 85 bytes, and the dict slot uses about 40 bytes at a normal load factor, so one entry costs about 130 bytes. At 167 events per second the middleware adds 167 × 86,400 = 14.4 million entries each day. 14.4 million × 130 bytes = about 1.9 GB each day. The dict also rehashes as it grows. A rehash of a 14 million entry dict copies 14 million slots at about 20 ns each, which is about 0.3 s in one step that blocks the consumer loop. The rehash needs a second table at the same time, so the peak memory doubles. The insert itself costs about 100 ns for each event.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete line retry.py:20 and line retry.py:25. `__call__` then only returns the event. If the retry counts must survive across calls, put them in a bounded structure and delete the entry when `with_retry` returns or raises. Measure the resident memory of the process over a one-hour run at a fixed event rate.

### [P1] MemoryStorage keeps every event for each topic and defeats its own size limit

- **Category**: Allocation and Copies
- **Location**: eventbus/storage/memory.py:13, eventbus/storage/memory.py:19-21
- **Hot path**: `MemoryStorage.store` (memory.py:16) accepts one event, so a caller runs it once for every event. The consumer loop at bus.py:58 is the intended caller. No caller exists inside this package, so treat the call rate as an assumption.
- **Problem**: `self._events` is a `deque` with `maxlen=max_events`, and the default limit is 100,000 (memory.py:12). That structure is bounded. `self._by_topic[event.topic]` is a plain list, and it is not bounded. When the deque drops its oldest event, the list in `_by_topic` still holds a reference, so Python frees nothing. The class docstring promises "a configurable max size" (memory.py:11), and the real memory has no limit. `get_by_topic` returns only `events[-limit:]` (memory.py:26), so no code ever reads the older entries. A comment above the class states that the class is clean. That comment asserts quality, and it gives no reason for the list without a limit, so this report still reports the growth.
- **Evidence**:
  ```python
  self._events: deque[Event] = deque(maxlen=max_events)
  self._by_topic: dict[str, list[Event]] = {}
  ```
  ```python
  self._by_topic[event.topic].append(event)
  ```
- **Estimate**: This is an estimate. At 167 events per second and 600 bytes for each event, `_by_topic` grows 167 × 86,400 × 600 bytes = about 8.6 GB each day. The `max_events` default of 100,000 promises a ceiling of 100,000 × 600 bytes = about 60 MB. The real number is 140 times that size after one day, and it keeps growing. The garbage collector cannot free the events, and each pass over the old generation must trace all of them. A full collection over 14 million reachable objects takes seconds.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Make each topic bucket a `deque(maxlen=max_events)` instead of a list. Then both structures respect the same limit. As an alternative, delete `_by_topic` and filter `_events` inside `get_by_topic`. That change trades O(1) topic access for one scan of the deque, so choose it only when the number of topics is small. Measure the resident memory of the process over a one-hour run at a fixed event rate.

### [P1] The dead-letter list grows without a limit

- **Category**: Allocation and Copies
- **Location**: eventbus/bus.py:24, eventbus/bus.py:94-95
- **Hot path**: `EventBus._process_loop` appends to the list at bus.py:95, once for every event that no handler consumes.
- **Problem**: The code appends the event and never removes it. Two paths reach the append. The first path is an event that matches no pattern in `self._handlers` (bus.py:85-86). The second path is an event where every handler raises, because the loop sets `dispatched = True` only after a handler returns (bus.py:88-92). A missing subscription in the configuration therefore sends 100% of the events into the list. The append itself is cheap. The memory is the problem.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []  # BUG [PERFORMANCE]: Unbounded list grows forever
  ```
  ```python
  if not dispatched:
      self._dead_letter.append(processed_event)
  ```
- **Estimate**: This is an estimate. The append costs about 60 ns, so the CPU cost is small. At 167 events per second and 600 bytes for each event, a rate of 1% unhandled events adds 1.67 × 86,400 × 600 bytes = about 86 MB each day. If one subscription is absent, all events go to the list: 14.4 million × 600 bytes = about 8.6 GB each day. The events stay reachable, so the garbage collector must trace all of them on each pass over the old generation.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change the field to `self._dead_letter: deque[Event] = deque(maxlen=10000)`. A `deque` still supports `len()`, so `status()` (bus.py:104) and the assert at tests/test_bus.py:52 keep working without a change. Add a `drain_dead_letters()` method so an operator can read and clear the events. Add a counter of dropped events, so the operator sees when the limit discards events. Measure the resident memory of the process over a one-hour run with no subscribers.

### [P1] The bus processes one event at a time and awaits each handler in series

- **Category**: I/O and Round Trips
- **Location**: eventbus/bus.py:85-92, eventbus/bus.py:51, eventbus/bus.py:58
- **Hot path**: `EventBus.start` creates exactly one `_process_loop` task at bus.py:51. That loop awaits one handler at a time at bus.py:89, and it reads the next event only after the last handler returns.
- **Problem**: The dispatch path has no concurrency at two levels. Inside one event, the loop awaits handler 1 to completion before it starts handler 2, although the handlers are independent. Across events, one consumer task means event N+1 waits for event N. Both costs add up. With I/O-bound handlers the loop spends nearly all of its time in `await` with no other work in flight. Note the trade-off: the current code gives strict FIFO order and no overlap between events. Some callers depend on that order, so the fix must keep the serial behaviour as the default.
- **Evidence**:
  ```python
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
          for handler in handlers:
              try:
                  await handler(processed_event)
  ```
- **Estimate**: This is an estimate. Assume 3 handlers match one event, and each handler takes 100 ms of I/O. The serial loop takes 3 × 100 ms = 300 ms for each event, so the bus carries 3.3 events per second. Concurrent handlers take 100 ms for each event, so the bus carries 10 events per second. That is a 3 times gain, and the gain scales with the number of handlers. Add 8 consumer tasks on top of that, and the rate reaches about 80 events per second. The total gain against the current code is about 24 times. The design rate is 167 events per second, so the bus needs both changes plus the fix in the webhook finding to reach it.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Collect the matching handlers into one list first. Then run them with `await asyncio.gather(*(h(event) for h in matched), return_exceptions=True)`. Read the returned exceptions and log each one, which keeps the current behaviour at bus.py:91-92. Add a `concurrency: int = 1` parameter to `EventBus.__init__`. Start that many `_process_loop` tasks in `start()`, and default to 1 so the order does not change for existing callers. Store the task objects on the instance, because the current code drops the reference at bus.py:51. Measure events per second and the delay from `publish` to the last handler.

### [P2] The logging middleware serializes every event before it checks the log level

- **Category**: Observability Overhead
- **Location**: eventbus/middleware/logging.py:18-30
- **Hot path**: `EventBus._process_loop` runs the middleware chain at bus.py:68-70, once for every event.
- **Problem**: The code builds a dict of 4 or 5 keys and then calls `json.dumps` on it, inside the argument list of `logger.log`. Python evaluates the argument before the call. `logger.log` checks the level after that, so the work is a total waste when the level is off. The default is `include_payload: bool = True` (logging.py:12), so `json.dumps` walks the whole payload for every event.
- **Evidence**:
  ```python
  logger.log(self.level, json.dumps(log_data))
  ```
- **Estimate**: This is an estimate. The dict build costs about 300 ns. `json.dumps` on a 200-byte payload costs about 10 µs, in the range of 5 µs to 15 µs. The total is about 10 µs for each event. At 167 events per second the cost is 1.7 ms per second. At 1,000 events per second the cost is 10 ms per second, which is 1% of one core. A 10 KB payload raises `json.dumps` to about 200 µs to 400 µs, so at 167 events per second the cost reaches about 50 ms per second. The level check removes 100% of this cost when the logger is off.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `if not logger.isEnabledFor(self.level): return event` as the first line of `__call__`. That check costs about 100 ns. Measure the CPU time of `LoggingMiddleware.__call__` with `cProfile`, with the logger at `WARNING` and the middleware level at `INFO`.

### [P2] The events table has no index that serves the sort in get_by_topic

- **Category**: I/O and Round Trips
- **Location**: eventbus/storage/sqlite.py:29, eventbus/storage/sqlite.py:54-58
- **Hot path**: No caller of `get_by_topic` exists inside this package. It is the read API of the storage backend, so a request handler or a dashboard calls it. Treat the call rate as an assumption. The cost grows with the number of rows in the table, and `SQLiteStorage` has no retention limit and no prune method, so the row count grows without a limit.
- **Problem**: The index is `idx_topic ON events(topic)`. The query filters on `topic` and then sorts on `timestamp DESC`. The index gives the rows for one topic, and it does not give them in timestamp order. SQLite must therefore read every matching row and sort all of them, and only then apply the `LIMIT`. Each row needs one lookup in the main b-tree by rowid, which is a random read.
- **Evidence**:
  ```python
  await self._db.execute(
      "CREATE INDEX IF NOT EXISTS idx_topic ON events(topic)"
  )
  ```
  ```python
  "SELECT event_id, topic, payload, timestamp, source "
  "FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
  ```
- **Estimate**: This is an estimate. Assume 100,000 rows and 10 topics, which gives 10,000 rows for each topic. The 100,000 row figure comes from the `max_events: int = 100000` default of the sibling backend at memory.py:12. The query reads 10,000 rows. With the pages in the operating system cache, each row costs about 1 µs, which is 10 ms. With cold pages, each row costs one random 4 KB read from the SSD at 20 µs, which is 200 ms. The sort adds 10,000 × log2(10,000) = about 133,000 comparisons, which is 2 ms to 13 ms. The total is about 12 ms warm and up to 213 ms cold, to return 100 rows. A composite index lets SQLite walk the index in order and stop after 100 rows: about 0.1 ms warm and about 2 ms cold. The saving is about 10 to 100 times on this path. The extra index costs one more b-tree write for each `INSERT`, which is about 10 µs to 20 µs.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `CREATE INDEX IF NOT EXISTS idx_topic_ts ON events(topic, timestamp DESC)` next to sqlite.py:29. Drop `idx_topic`, because the new index also serves a filter on `topic` alone. Run `EXPLAIN QUERY PLAN` on the query before and after the change. Look for the line `USE TEMP B-TREE FOR ORDER BY` before the change, and confirm that it is absent after the change.

### [P2] get_recent copies the whole deque to return the last 100 events

- **Category**: Allocation and Copies
- **Location**: eventbus/storage/memory.py:30
- **Hot path**: No caller exists inside this package. It is a read API, and a status page or a dashboard polls it. Treat the call rate as an assumption. The cost grows with `max_events`, which defaults to 100,000 (memory.py:12).
- **Problem**: `list(self._events)` copies every element of the deque into a new list. The slice `[-limit:]` then throws away all but the last `limit` elements and allocates a second list. The default `limit` is 100, so the code copies up to 100,000 references to keep 100 of them.
- **Evidence**:
  ```python
  return list(self._events)[-limit:]
  ```
- **Estimate**: This is an estimate. 100,000 pointer copies at about 10 ns each cost about 1 ms. The intermediate list holds 100,000 pointers at 8 bytes each, which is 800 KB of garbage for each call. The reference count of each of the 100,000 events also changes twice. The fixed version copies 100 items, which costs about 1 µs and about 800 bytes. The saving is about 1 ms of CPU and about 800 KB of garbage for each call, and the code does 1,000 times fewer copies. One call each second wastes 1 ms per second of CPU and adds 800 KB per second of garbage, which raises the frequency of the garbage collector passes.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Take only the items that the caller needs. Use `list(itertools.islice(reversed(self._events), limit))[::-1]`. A `deque` supports `reversed` in O(1) for each step, so this touches `limit` items and no more. Measure the allocated bytes with `tracemalloc` around one call, with 100,000 events in the deque.

### [P2] The dispatch loop tests every registered pattern for every event

- **Category**: Data Layout
- **Location**: eventbus/bus.py:85-86, eventbus/bus.py:34, eventbus/event.py:23-25
- **Hot path**: `EventBus._process_loop` runs this loop at bus.py:85, once for every event.
- **Problem**: The loop is a full scan of `self._handlers` for each event, so the work is O(P) where P is the number of registered patterns. A dict lookup by topic answers the exact-match case in O(1), and only the wildcard patterns need a scan. Two costs add to the scan. `Event.matches` builds two new strings for each wildcard pattern on each call: `pattern[:-2]` and `prefix + "."` (event.py:24-25). The subscribe count also never falls, because `unsubscribe` calls `self._handlers[pattern].remove(handler)` (bus.py:34) and leaves the empty list in the `defaultdict`. An empty bucket still costs one full `matches` call for every event, for the life of the process.
- **Evidence**:
  ```python
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
  ```
  ```python
  if pattern.endswith(".*"):
      prefix = pattern[:-2]
      return self.topic == prefix or self.topic.startswith(prefix + ".")
  ```
- **Estimate**: This is an estimate. One pass costs a loop step, a method call, and the body of `matches`: about 300 ns. A wildcard pattern adds two string allocations at 50 ns to 100 ns each, so about 150 ns more. With 50 patterns the scan costs 50 × 300 ns = 15 µs for each event, which is 2.5 ms per second at 167 events per second. With 500 patterns the scan costs 150 µs for each event, which is 25 ms per second at 167 events per second and 150 ms per second at 1,000 events per second. The exact-match lookup replaces the scan with one dict probe of about 50 ns, so the saving grows with P.
- **Impact**: Medium
- **Effort**: Medium (hours)
- **Suggested fix**: Hold two structures instead of one. Put the exact patterns in a dict keyed by the topic, and read it with one lookup. Keep only the wildcard patterns in a list. Store the precomputed prefix beside each wildcard pattern at subscribe time, so the test is one `startswith` call with no allocation. Delete the key from both structures when its handler list becomes empty. Update `subscribe`, `unsubscribe`, and `status` together, because all three read `self._handlers`. Measure the CPU time of `_process_loop` with `cProfile` at 50 patterns and at 500 patterns.

### [P3] The consumer loop creates a task and a timer for every event

- **Category**: Redundant Work
- **Location**: eventbus/bus.py:62
- **Hot path**: `EventBus._process_loop` runs this line once for every event (bus.py:58-64).
- **Problem**: `asyncio.wait_for` wraps the coroutine in a `Task` and arms a timer for every call. It then cancels the timer when the event arrives. The only reason for the timeout is the shutdown check `while self._running` on the line above. A sentinel value on the queue gives the same shutdown behaviour with a bare `await self._queue.get()`.
- **Evidence**:
  ```python
  event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
  ```
- **Estimate**: This is an estimate. The task creation, the timer setup, and the timer cancel cost about 10 µs together, in the range of 5 µs to 15 µs. A bare `await self._queue.get()` costs about 1 µs when an item is ready. The waste is about 9 µs for each event. At 167 events per second the cost is 1.5 ms per second, which is under 0.2% of one second. At 10,000 events per second the cost is 90 ms per second, which is 9% of one core. The idle case adds 10 wakeups per second, which is not measurable.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Define one module-level sentinel object. Make `stop()` set `self._running = False` and then put the sentinel on the queue. Make the loop `await self._queue.get()` with no timeout, and break when it reads the sentinel. This change also removes the lost-event defect that the comment at bus.py:56 records, because the loop can then drain the queue up to the sentinel. Measure the CPU time of `_process_loop` at 5,000 events per second.

## Cold-Code Notes

- eventbus/bus.py:74, eventbus/bus.py:92 — `logger.warning(f"Middleware error: {e}")` and `logger.error(f"Handler error for {pattern}: {e}")` format the message eagerly, but these lines run only when a middleware or a handler raises.
- eventbus/middleware/retry.py:37, eventbus/middleware/retry.py:40 — eager f-strings in the retry log lines. They run only after a handler fails, and the code then sleeps 1 s or more at retry.py:41, so the format cost is not measurable.
- eventbus/handlers/webhook.py:45, eventbus/handlers/webhook.py:47 — eager f-strings on the two webhook error paths.
- eventbus/storage/sqlite.py:16-31 — `initialize` runs `CREATE TABLE`, `CREATE INDEX`, and one commit. That is about 5 ms to 15 ms, once for each process. The cost stays small even in a serverless runtime that restarts often.
- eventbus/handlers/webhook.py:52-57 — `create_webhook_handler` builds a new headers dict for each call. It runs once for each handler, at setup.
- tests/test_bus.py:18, tests/test_bus.py:38, tests/test_bus.py:49 — each test calls `await asyncio.sleep(0.2)` to wait for the consumer. The 3 tests spend 0.6 s of the suite wall-clock time in sleep. An `asyncio.Event` that the handler sets would cut that to milliseconds. The total is 0.6 s, so the suite does not block CI today. The cost grows in a linear way as the authors add tests with the same pattern.

## Not Found

- **Algorithms**: apart from the deduplication scan, no other O(N²) pattern exists. The package has no nested loop over the same collection, no `in list` membership test inside a loop, no full sort for a top-k query, and no string concatenation in a loop. `Event.matches` (event.py:19-26) is O(1) in the length of the topic.
- **Allocation and Copies**: the package makes no deep copy. It has no call to `copy.deepcopy` and no `json.loads(json.dumps(...))` pattern. `Event` is a frozen dataclass, so the bus passes references and never needs a defensive copy. `MemoryStorage.get_by_topic` (memory.py:26) slices with `events[-limit:]`, which copies only `limit` items — this is correct, unlike `get_recent` on line 30.
- **Data Layout**: an array or a bit vector does not apply here. The keys are topic strings and uuid strings, not small dense integers. `Event` is a frozen dataclass without `slots=True`. `slots=True` would save about 100 bytes out of about 600 bytes for each instance, which is under 20%, and `Event` is a public class, so the change can break a subclass. This is dismissed as Low.
- **Redundant Work**: `uuid.uuid4()` runs once for each new event (event.py:15) and costs about 1.5 µs. At 167 events per second that is 0.25 ms per second, which is under 0.03% of one second. This is dismissed. `time.time()` runs twice for each event in the deduplication middleware (dedup.py:21, dedup.py:26) and costs about 100 ns in total. This is dismissed on its own, and the fix belongs to the deduplication finding above.
- **Observability Overhead**: the package collects no metrics, no counters, and no percentiles, and it has no debug timer and no tracing on the dispatch path. `status()` (bus.py:98-105) builds a dict comprehension over all patterns, and an operator calls it rarely, so it is dismissed.
- **Checked and correct**: the query at sqlite.py:54-58 uses parameter binding, not string format. The comment on lines 51-53 marks it as a false-positive trap, and the code is correct. Parameter binding also lets SQLite reuse the prepared statement, so it is the faster form as well.