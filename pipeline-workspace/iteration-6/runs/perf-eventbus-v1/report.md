I have read all 510 lines of the codebase and mapped the one in-repo loop that multiplies every cost. Here is the report.

---

# Performance Audit Report

## Summary

The `eventbus` package is a Python 3.11 in-process publish and subscribe bus. It has one asynchronous loop, `EventBus._process_loop` at `eventbus/bus.py:58`, which takes each event from a queue, runs the middleware chain, and then calls the matched handlers. This loop is the single hot path: it multiplies the cost of the three middleware classes, of both storage classes, and of the webhook handler. The queue holds 10,000 events (`eventbus/bus.py:22`), so the design expects bursts of that size, but four separate per-event costs hold the real throughput far below that. The single best quick win is `DeduplicationMiddleware._cleanup_expired` at `eventbus/middleware/dedup.py:24`: it scans up to 10,000 dictionary entries for every event, which costs about 600 µs per event and alone caps the bus at about 1,600 events per second.

Assumption for all estimates below: the bus carries 1,000 events per second, and one core runs the loop. Each estimate gives the per-event cost, so you can rescale it for your rate. Every number below is an estimate. Bash execution was not permitted in this session, so the CPU costs come from the latency table and from the anchor of 10–100 ns for one interpreted statement, not from a measurement on this machine.

## Findings

### [P1] The deduplication middleware scans all 10,000 entries for every event

- **Category**: Algorithms
- **Location**: `eventbus/middleware/dedup.py:16`, `eventbus/middleware/dedup.py:24-33`, `eventbus/middleware/dedup.py:42`
- **Hot path**: `EventBus._process_loop` awaits each middleware at `eventbus/bus.py:70`, inside the loop that starts at `eventbus/bus.py:60`. `__call__` calls `self._cleanup_expired()` on line 16 as its first action, so the scan runs one time for every event.
- **Problem**: The cleanup method builds a full list comprehension over `self._seen` on every call. The dictionary holds up to `max_seen=10000` entries (`eventbus/middleware/dedup.py:9`). The work is O(n) for each event, but the method almost always deletes nothing, because an entry expires only after `window_seconds=60.0`. A second waste sits on line 42: when the dictionary goes over the cap, the code allocates a new 10,000-element list of keys and then uses only the first few.
- **Evidence**:
  ```python
  expired = [k for k, ts in self._seen.items()
             if now - ts > self.window_seconds]
  ```
  and, on line 42:
  ```python
  for k in list(self._seen.keys())[:excess]:
  ```
- **Estimate**: Each entry costs a dictionary iteration step, a tuple unpack, a float subtraction, and a compare. That is about 4 interpreted operations, so about 60 ns per entry. 10,000 entries × 60 ns = about 600 µs per event. At 1,000 events per second the scan needs 600 ms of CPU per second, which is 60% of one core. The scan alone caps this middleware at 1/600 µs = about 1,660 events per second. Line 42 adds a 10,000-pointer list allocation of about 80 KB per event, which costs about another 100–200 µs. A deque that pops only the entries that truly expired makes the amortized cost about 1 µs per event. The saving is about 600x on this middleware.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a `collections.deque` of `(event_id, timestamp)` pairs in insertion order. Pop from the left of the deque while the head timestamp is older than the window, and delete the matching key from `self._seen`. Pop from the left also when `len(self._seen)` goes over `max_seen`. Delete the list comprehension and the `list(self._seen.keys())` call. To confirm the win, profile the middleware with `cProfile` and check that the time in `_cleanup_expired` drops below 1% of the loop time. Then measure events per second through the middleware alone.

### [P1] The webhook handler makes a blocking HTTP call and stalls the whole event loop

- **Category**: I/O and Round Trips
- **Location**: `eventbus/handlers/webhook.py:43`, `eventbus/handlers/webhook.py:19`
- **Hot path**: `EventBus._process_loop` awaits each handler at `eventbus/bus.py:89`. A webhook subscriber runs one time for every matched event.
- **Problem**: `urllib.request.urlopen` is synchronous. The handler is a coroutine, so the call blocks the thread that runs the event loop. Every other coroutine in the process stops for the length of the HTTP round trip, not only the bus loop. The default `timeout` is 30 seconds (`eventbus/handlers/webhook.py:19`), so one slow endpoint freezes the whole process for up to 30 seconds.
- **Evidence**:
  ```python
  response = urllib.request.urlopen(req, timeout=self.timeout)
  ```
- **Estimate**: One round trip in the same datacenter costs 50 µs, plus server time of about 5–50 ms. One round trip from California to the Netherlands costs 150 ms. Take a remote endpoint at 150 ms. The process then handles at most 1/150 ms = about 6 events per second, and every unrelated task in the process waits too. A non-blocking call keeps the loop free, so the loop can serve other work during those 150 ms. The saving on a remote endpoint is about 150 ms of loop stall for every event.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the call with `await asyncio.to_thread(urllib.request.urlopen, req, timeout=self.timeout)`. This moves the block to a worker thread and frees the loop. Note the limit: the default executor holds `min(32, cpu_count + 4)` threads, so the thread pool caps the concurrency. For a full fix, replace `urllib` with `httpx.AsyncClient` or `aiohttp`; that fix also solves the next finding about connection reuse. To confirm the win, measure the loop lag with `loop.slow_callback_duration` or with `asyncio` debug mode. The lag must drop from the round trip time to under 1 ms.

### [P1] SQLite storage commits one time for every event

- **Category**: I/O and Round Trips
- **Location**: `eventbus/storage/sqlite.py:45`, `eventbus/storage/sqlite.py:39-44`, `eventbus/storage/sqlite.py:16-31`
- **Hot path**: `EventBus._process_loop` awaits each handler at `eventbus/bus.py:89`. The package description in `pyproject.toml:4` states "persistent storage", so a `SQLiteStorage.store` handler runs one time for every matched event.
- **Problem**: `store` calls `await self._db.commit()` after each single-row insert. One commit costs one fsync. The `initialize` method (`eventbus/storage/sqlite.py:16-31`) also does not set `journal_mode` or `synchronous`, so SQLite keeps the defaults of `journal_mode=DELETE` and `synchronous=FULL`. Those defaults make each commit cost more than one fsync, because SQLite must also sync the rollback journal and the directory entry.
- **Evidence**:
  ```python
  await self._db.commit()  # BUG [PERFORMANCE]: Commits after every single insert
  ```
- **Estimate**: One commit or fsync costs about 1–10 ms. Take 5 ms. At 1,000 events per second the handler needs 1,000 × 5 ms = 5,000 ms of fsync for each second of events. The handler cannot keep up, so the queue of 10,000 events (`eventbus/bus.py:22`) fills, and `publish` at `eventbus/bus.py:46` blocks the producer. One commit per event caps throughput at 1/5 ms = about 200 events per second. A batch of 100 rows turns 100 fsyncs (500 ms) into 1 fsync (5 ms), which is a 100x cut in the fsync cost.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Add a `store_many(events)` method that uses `executemany` for the insert and commits one time. Buffer events in `store` and flush the buffer at 100 rows, or after 10 ms, whichever comes first. Flush also in `close` (`eventbus/storage/sqlite.py:73`). Add `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` to `initialize`. State the trade-off to the caller: a buffer of 100 rows can lose up to 100 events on a crash. To confirm the win, measure inserts per second, and count the fsync calls with `fs_usage` on macOS or `strace -c` on Linux.

### [P1] The dispatch loop awaits handlers one at a time, so handler latencies add up

- **Category**: I/O and Round Trips
- **Location**: `eventbus/bus.py:83-92`
- **Hot path**: The loop at `eventbus/bus.py:60`. The bus runs one `_process_loop` task (`eventbus/bus.py:51`), and that task awaits every handler for one event before it takes the next event from the queue.
- **Problem**: The handlers are independent of each other, but the code awaits them in sequence. Throughput becomes 1 divided by the sum of all handler latencies. One slow handler holds back every other subscriber and every later event. The queue can hold 10,000 events, but the loop drains it at the speed of the slowest chain.
- **Evidence**:
  ```python
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
          for handler in handlers:
              try:
                  await handler(processed_event)
  ```
- **Estimate**: Assume 3 subscribers, and each one makes one call of 5 ms. One event then costs 3 × 5 ms = 15 ms, so the bus carries 1/15 ms = about 66 events per second. `asyncio.gather` runs the 3 calls together, so one event costs about 5 ms and the bus carries about 200 events per second. That is a 3x saving, and it grows with the number of subscribers. A concurrency cap of 20 events in flight raises the ceiling to about 20 × 200 = 4,000 events per second.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Collect the matched handlers into one list. Then call `await asyncio.gather(*(h(ev) for h in matched), return_exceptions=True)`. Inspect the returned list for exceptions and log them, so the current per-handler error behaviour at `eventbus/bus.py:91` stays. Next, run each event as a task under an `asyncio.Semaphore` to bound the concurrency. State the trade-off: the current code delivers events in strict order, and concurrent tasks drop that guarantee. Keep one task per topic if the callers need order per topic. To confirm the win, measure events per second with a handler that sleeps 5 ms.

### [P1] The dispatch loop tests every registered pattern for every event

- **Category**: Algorithms
- **Location**: `eventbus/bus.py:85-86`, `eventbus/event.py:19-26`, `eventbus/bus.py:34`, `eventbus/bus.py:20`
- **Hot path**: The loop at `eventbus/bus.py:60`. Line 85 iterates all of `self._handlers`, and line 86 calls `processed_event.matches(pattern)` one time for each registered pattern, for every event.
- **Problem**: The dispatch is O(P) for P patterns, where one dictionary lookup gives the answer for an exact topic. `matches` also repeats work that depends only on the pattern, not on the event. Line 24 of `eventbus/event.py` slices `pattern[:-2]`, and line 25 builds `prefix + "."`; both allocate a new string on every call. A second problem makes P grow without bound: the `unsubscribe` function at `eventbus/bus.py:34` removes the handler from the list but never deletes the dictionary key. `self._handlers` is a `defaultdict` (`eventbus/bus.py:20`), so a pattern with zero handlers stays in the dictionary. The loop then calls `matches` for that dead pattern on every event, forever.
- **Evidence**:
  ```python
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
  ```
  and, in `eventbus/event.py:23-25`:
  ```python
  if pattern.endswith(".*"):
      prefix = pattern[:-2]
      return self.topic == prefix or self.topic.startswith(prefix + ".")
  ```
- **Estimate**: One `matches` call costs a method call plus 2 to 4 string operations, which is about 200 ns. With 100 patterns: 100 × 200 ns = 20 µs per event, so 20 ms per second at 1,000 events per second, which is 2% of one core. With 1,000 patterns: 200 µs per event, so 20% of one core at the same rate. A split index makes the cost one exact-match lookup of about 40 ns, plus one lookup for each dot prefix of the topic. A topic such as `orders.created` has 2 prefixes, so the total is about 150 ns per event, and it does not grow with P. The saving is about 130x at 100 patterns and about 1,300x at 1,000 patterns.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Build three structures at subscribe time. Keep an exact dictionary from topic to handlers. Keep a prefix dictionary from the stripped prefix to handlers for each `x.*` pattern, so the slice and the concatenation happen one time per subscription. Keep one list for the `*` pattern. In dispatch, do one lookup in the exact dictionary, then walk the dot prefixes of the topic, then add the `*` list. Also delete the dictionary key in `unsubscribe` when the handler list becomes empty. To confirm the win, measure events per second at 10, 100, and 1,000 subscriptions. The three numbers must stay within 10% of each other.

### [P2] The logging middleware serializes every event even when the log level is off

- **Category**: Observability Overhead
- **Location**: `eventbus/middleware/logging.py:18-30`, `eventbus/middleware/logging.py:12`
- **Hot path**: `EventBus._process_loop` awaits each middleware at `eventbus/bus.py:70`, so `__call__` runs one time for every event.
- **Problem**: The method builds a 4-key dictionary, adds the payload, and then calls `json.dumps` on line 30. It does this before the logging library decides whether any handler wants the record. The code never calls `logger.isEnabledFor`. `include_payload` defaults to `True` (`eventbus/middleware/logging.py:12`), so the middleware serializes the full payload for every event. If the level is off, or no handler is attached, the whole cost is waste.
- **Evidence**:
  ```python
  logger.log(self.level, json.dumps(log_data))
  ```
- **Estimate**: `json.dumps` of a record with 4 metadata fields and a 10-key payload costs about 10 µs. The dictionary build adds about 0.5 µs. At 1,000 events per second: 1,000 × 10.5 µs = about 10.5 ms per second, which is 1% of one core. At 10,000 events per second it is 10% of one core. A level check costs about 0.2 µs, so an early return saves about 98% of the middleware cost when the level is off. The saving when the level is on is smaller: lazy formatting still pays the `json.dumps` cost, because a handler consumes the record.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `if not logger.isEnabledFor(self.level): return event` as the first line of `__call__`. Then wrap the dictionary in a small class whose `__str__` calls `json.dumps`, and pass it as `logger.log(self.level, "%s", wrapper)`. The logging library then formats the record only when a handler emits it. To confirm the win, measure events per second with the logger level set to `WARNING` and with `self.level` at `INFO`.

### [P2] The dead-letter list has no bound and grows for the life of the process

- **Category**: Allocation and Copies
- **Location**: `eventbus/bus.py:24`, `eventbus/bus.py:94-95`
- **Hot path**: The loop at `eventbus/bus.py:60`. Line 95 appends to the list for every event that no handler took.
- **Problem**: `self._dead_letter` is a plain list, and no code ever removes an item from it. The only reader is `status` at `eventbus/bus.py:104`, and it reads the length only. The `Event` objects therefore stay alive forever with no consumer. The dispatch code makes this worse: line 90 sets `dispatched = True` only after a handler returns without an exception. An event whose only handler raises therefore also goes to the dead-letter list.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []  # BUG [PERFORMANCE]: Unbounded list grows forever
  ```
  and, at line 94:
  ```python
  if not dispatched:
      self._dead_letter.append(processed_event)
  ```
- **Estimate**: One `Event` with a small payload costs about 500 bytes: the dataclass, the 36-character `event_id` string, and the payload dictionary. At 100 unmatched events per second: 500 B × 100 × 3,600 = about 180 MB per hour. At 1,000 unmatched events per second: about 1.8 GB per hour. The process reaches out-of-memory. Long before that, the objects move into the old generation, so every full garbage collection walks more objects and takes longer. A `deque` with `maxlen=1000` holds the memory at about 500 KB, which is a constant.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change the field to `collections.deque(maxlen=1000)`. The append cost stays the same, and the deque drops the oldest event when it is full. If the callers need the events, add a `drain_dead_letters()` method, or send them to a storage backend. To confirm the win, run 1,000,000 unmatched events and watch the resident set size stay flat.

### [P2] The retry middleware writes to a dictionary that nothing reads and nothing trims

- **Category**: Redundant Work
- **Location**: `eventbus/middleware/retry.py:25`, `eventbus/middleware/retry.py:20`
- **Hot path**: `EventBus._process_loop` awaits each middleware at `eventbus/bus.py:70`, so `__call__` runs one time for every event.
- **Problem**: `__call__` writes `self._retry_counts[event.event_id] = 0` for every event. No code reads `self._retry_counts`. A search of the package finds only the write on line 25 and the declaration on line 20. `with_retry` (`eventbus/middleware/retry.py:28`) does not read it either, and the bus never calls `with_retry`. Nothing deletes a key, so the dictionary grows for the life of the process. The whole cost of this middleware is waste.
- **Evidence**:
  ```python
  # Track that this event entered the retry middleware
  self._retry_counts[event.event_id] = 0
  ```
- **Estimate**: Each entry holds a 36-character string of about 85 bytes, plus about 100 bytes for the dictionary slot and the integer reference. That is about 185 bytes per event. At 1,000 events per second: 185 B × 1,000 × 3,600 = about 670 MB per hour. The process reaches out-of-memory. The CPU cost is small: the dictionary write costs about 60 ns per event, or 0.06 ms per second at 1,000 events per second. The memory saving is the whole 670 MB per hour.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete line 25 and the `_retry_counts` field on line 20. If the retry count must persist for real use, key it inside `with_retry` and delete the key when the handler returns or raises for the last time. Also connect `with_retry` to the dispatch code at `eventbus/bus.py:89`; the middleware has no effect on retries today. To confirm the win, run 1,000,000 events and watch the resident set size stay flat.

### [P2] The SQLite index does not cover the sort, so the query sorts every row of the topic

- **Category**: I/O and Round Trips
- **Location**: `eventbus/storage/sqlite.py:28-30`, `eventbus/storage/sqlite.py:54-58`
- **Hot path**: `get_by_topic` is the read path of the storage class. No code in this repository calls it, so the frequency belongs to the application that reads history. The cost below is the cost for one call.
- **Problem**: The index is `events(topic)` only. The query filters on `topic` and orders by `timestamp DESC` with a `LIMIT`. SQLite uses the index to find the rows for the topic, but the index does not hold the rows in timestamp order. SQLite must therefore read every row for that topic and sort them in a temporary B-tree before it applies the `LIMIT 100`.
- **Evidence**:
  ```python
  "CREATE INDEX IF NOT EXISTS idx_topic ON events(topic)"
  ```
  and, at lines 55-57:
  ```python
  "SELECT event_id, topic, payload, timestamp, source "
  "FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
  ```
- **Estimate**: Assume 100,000 rows for one hot topic and the default `limit=100`. With a warm page cache, SQLite reads 100,000 rows at memory speed and sorts 100,000 keys, which costs about 50–100 ms of CPU. With a cold cache, the row reads are random 4 KB SSD reads at 20 µs each; even a 10% miss rate adds 10,000 × 20 µs = 200 ms. A composite index lets SQLite walk 100 index entries in reverse order and stop, which costs about 1 ms. The saving is about 50x to 200x for each call.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the index with `CREATE INDEX IF NOT EXISTS idx_topic_ts ON events(topic, timestamp DESC)`. Drop `idx_topic`; the composite index serves the same equality lookups on `topic`. To confirm the win, run `EXPLAIN QUERY PLAN` on the query. The plan must name `idx_topic_ts` and must not contain `USE TEMP B-TREE FOR ORDER BY`.

### [P2] The webhook handler opens a new TCP and TLS connection for every event

- **Category**: I/O and Round Trips
- **Location**: `eventbus/handlers/webhook.py:33-43`
- **Hot path**: `EventBus._process_loop` awaits each handler at `eventbus/bus.py:89`, so the handler builds a new request and a new connection for every matched event.
- **Problem**: `urllib.request.urlopen` does not pool connections. It opens a new socket for each call and closes it after the response. Each event therefore pays a TCP handshake, and an `https` URL also pays a TLS handshake. The handler holds `self.url`, `self.headers`, and `self.timeout` across calls, but it cannot hold the connection, because `urllib` has no pool.
- **Evidence**:
  ```python
  req = urllib.request.Request(
      self.url,
      data=payload,
      headers=self.headers,
      method="POST",
  )
  ...
  response = urllib.request.urlopen(req, timeout=self.timeout)
  ```
- **Estimate**: A TCP handshake costs 1 round trip. A TLS 1.3 handshake costs 1 more round trip, and TLS 1.2 costs 2 more. Take a remote endpoint at 150 ms per round trip and TLS 1.2: the handshakes cost about 450 ms, and the POST costs 150 ms. A reused connection pays only the 150 ms, so the saving is about 450 ms per event, which is 75% of the delivery latency. In the same datacenter at 50 µs per round trip, the handshakes cost about 150 µs, and the fresh key exchange costs about 1–2 ms of CPU on both ends. The saving there is about 1–2 ms of CPU per event.
- **Impact**: Medium
- **Effort**: Medium (hours)
- **Suggested fix**: Replace `urllib` with one long-lived `httpx.AsyncClient` that the handler creates in `__init__` and closes in a new `aclose` method. Add `httpx` to the dependencies in `pyproject.toml:6`. This one change also fixes the P1 finding about the blocking call, so do both together. To confirm the win, count the open sockets to the endpoint with `netstat` while 1,000 events flow; the count must stay near the pool size. Then measure the p50 latency per delivery.

### [P2] The topic index of MemoryStorage grows without bound and breaks the documented limit

- **Category**: Allocation and Copies
- **Location**: `eventbus/storage/memory.py:19-21`, `eventbus/storage/memory.py:13`, `eventbus/storage/memory.py:10`
- **Hot path**: `EventBus._process_loop` awaits each handler at `eventbus/bus.py:89`, so a `MemoryStorage.store` handler runs one time for every matched event.
- **Problem**: `self._events` is a `deque` with `maxlen=max_events`, so it is bounded at 100,000 events by default. `self._by_topic` is a plain dictionary of plain lists, and nothing ever trims those lists. When the deque drops an old event, the same event stays in `_by_topic` forever. The class therefore does not hold the limit that its own docstring states on line 10: "Store events in memory with a configurable max size". The file carries a comment on line 8 that calls the class clean, but the unbounded dictionary contradicts the `max_events` bound.
- **Evidence**:
  ```python
  self._events.append(event)
  if event.topic not in self._by_topic:
      self._by_topic[event.topic] = []
  self._by_topic[event.topic].append(event)
  ```
- **Estimate**: One `Event` with a small payload costs about 500 bytes. The deque holds 100,000 events, so the bounded part costs about 50 MB. At 1,000 events per second, `_by_topic` adds 500 B × 1,000 × 3,600 = about 1.8 GB per hour with no ceiling. After 24 hours the dictionary holds about 43 GB, and the process reaches out-of-memory. A per-topic bound holds the total at the number of topics times the per-topic limit.
- **Impact**: Medium
- **Effort**: Medium (hours)
- **Suggested fix**: Give each topic a `collections.deque(maxlen=per_topic_max)` instead of a list. Then `get_by_topic` at line 26 must slice the deque with `itertools.islice`, because a deque does not support a slice. State the trade-off: the total memory becomes the topic count times `per_topic_max`, not one global bound. The other option keeps one global bound: store only ids in `_by_topic`, and read the events through the deque. To confirm the win, store 1,000,000 events across 10 topics and check that the resident set size stops growing.

### [P3] The process loop wraps every queue read in asyncio.wait_for

- **Category**: Redundant Work
- **Location**: `eventbus/bus.py:62`, `eventbus/bus.py:60`
- **Hot path**: The loop at `eventbus/bus.py:60` runs line 62 one time for every event.
- **Problem**: `asyncio.wait_for` wraps the coroutine in a task, schedules a timer with `call_later`, adds a done callback, and then cancels the timer. The code needs the 0.1-second timeout only so the loop can notice that `stop()` set `self._running` to `False` (`eventbus/bus.py:55`). A bare `await self._queue.get()` does the same read with none of the timer work.
- **Evidence**:
  ```python
  event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
  ```
- **Estimate**: The task creation, the timer handle, the done callback, and the cancel path cost about 5–10 µs per event. A bare `await queue.get()` on a queue that already holds an item costs about 1 µs. At 1,000 events per second the extra work costs 5–10 ms per second, which is 0.5% to 1% of one core. At 10,000 events per second it is 5% to 10% of one core. The idle cost is small: the loop wakes 10 times per second when the queue is empty.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the timeout with a sentinel. Make `stop()` set `self._running = False` and then put a module-level `_STOP` object on the queue. The loop then does `event = await self._queue.get()` and breaks when the event is `_STOP`. This also removes the up-to-0.1-second delay in `stop()`. To confirm the win, measure events per second with a handler that does nothing; the gain must be about 5–10 µs per event.

### [P3] The event factory builds a hyphenated UUID string for every event

- **Category**: Allocation and Copies
- **Location**: `eventbus/event.py:15`
- **Hot path**: `EventBus.publish` at `eventbus/bus.py:46` takes an `Event` that the caller built, so the default factory runs one time for every event that a caller creates without an explicit `event_id`. `create_event` at `eventbus/event.py:30` and the tests at `tests/test_bus.py:17` both use the default.
- **Problem**: `uuid.uuid4()` reads 16 random bytes through a `getrandom` system call and then builds a `UUID` object. `str()` then formats the 32 hexadecimal digits into a 36-character string with 4 hyphens. The bus uses the id only as a dictionary key in the dedup middleware (`eventbus/middleware/dedup.py:18`) and as a primary key in SQLite (`eventbus/storage/sqlite.py:21`). Neither use needs the hyphens.
- **Evidence**:
  ```python
  event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  ```
- **Estimate**: The system call, the object build, and the format cost about 2 µs in total. At 1,000 events per second that is 2 ms per second, which is 0.2% of one core. `uuid.uuid4().hex` skips the hyphen format and saves about 0.5 µs, or 0.05% of one core. The 36-byte string also drops to 32 bytes, which saves 4 KB per 1,000 events.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change the factory to `lambda: uuid.uuid4().hex`. Do not replace `uuid4` with a counter unless you measure a real need. A counter costs about 0.2 µs and saves about 1.8 µs, but it loses uniqueness across processes and changes the id format that stored rows already hold. To confirm the win, time 1,000,000 `Event` builds before and after.

### [P3] get_recent copies the whole 100,000-event deque to return 100 events

- **Category**: Allocation and Copies
- **Location**: `eventbus/storage/memory.py:30`, `eventbus/storage/memory.py:12`
- **Hot path**: No code in this repository calls `get_recent`. It is the read path of a public storage class, so the frequency belongs to the caller. The cost below is the cost for one call. Treat this as opportunistic work.
- **Problem**: `list(self._events)` materializes the full deque into a new list, and the slice then keeps only the last `limit` items. The deque holds up to `max_events=100000` events (`eventbus/storage/memory.py:12`), and the default `limit` is 100. The code therefore copies 100,000 pointers and throws away 99,900 of them.
- **Evidence**:
  ```python
  return list(self._events)[-limit:]
  ```
- **Estimate**: 100,000 pointer copies with a reference count increment for each one cost about 10 ns each, so about 1 ms per call. The new list holds 100,000 × 8 bytes = 800 KB, and the memory traffic alone costs about 64 µs at the rate of 1 MB per 64 µs. The garbage collector must then free the 800 KB list. `itertools.islice` over `reversed(self._events)` touches only 100 elements and costs about 10 µs. The saving is about 100x per call, plus the 800 KB allocation.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Use `list(itertools.islice(reversed(self._events), limit))[::-1]`. The reverse iterator walks the deque backward, and `islice` stops after `limit` items. The final slice restores the oldest-first order that the current code returns. To confirm the win, time one call with 100,000 stored events before and after.

### [P4] count() scans the whole table

- **Category**: I/O and Round Trips
- **Location**: `eventbus/storage/sqlite.py:69`
- **Hot path**: No code in this repository calls `count`. It is a read path of a public storage class. Record this item; do not schedule it.
- **Problem**: SQLite does not keep a row count, so `SELECT COUNT(*)` must visit every row or every entry of the smallest index. The cost grows with the table size.
- **Evidence**:
  ```python
  cursor = await self._db.execute("SELECT COUNT(*) FROM events")
  ```
- **Estimate**: With 1,000,000 rows and a warm page cache, the scan costs about 30–100 ms of CPU. With a cold cache the scan must read the pages from SSD; 1,000,000 rows at about 200 bytes each is about 200 MB, which costs about 200 ms at the rate of 1 MB per 1 ms. A counter row answers the same question in about 0.1 ms. The saving is about 300x to 2,000x per call, but the call is rare.
- **Impact**: Low
- **Effort**: Medium (hours)
- **Suggested fix**: The saving does not repay the work today. If a caller starts to poll `count` on a request path, add a single-row counter table and update it inside the same transaction as the insert. That change must also handle the `INSERT OR REPLACE` case at `eventbus/storage/sqlite.py:40`, because a replace does not add a row.

## Cold-Code Notes

- `eventbus/storage/sqlite.py:16-31`: `initialize` runs 3 statements and 1 commit at startup. The cost is one fsync, one time. The missing `journal_mode` and `synchronous` pragmas belong to the P1 commit finding.
- `eventbus/bus.py:74` and `eventbus/bus.py:92`: both lines build an f-string before the logging library decides to emit the record. Both sit on error paths that run rarely.
- `eventbus/bus.py:34`: `list.remove` is a linear scan over the handlers of one pattern. `unsubscribe` runs at setup and teardown, not per event.
- `eventbus/handlers/webhook.py:52-57`: `create_webhook_handler` builds a new headers dictionary for each call. Callers run it one time per subscription.
- `eventbus/bus.py:98-105`: `status` builds a dictionary comprehension over all patterns. It is an administrative read, and it computes on read, which is the correct choice.
- `tests/test_bus.py:18`, `tests/test_bus.py:38`, `tests/test_bus.py:49`: each test waits a fixed `asyncio.sleep(0.2)`. The suite holds 3 tests, so the total wall time is 0.6 s. Do not change it; the cost does not block CI.

## Not Found

- **Data Layout**: no finding. The code holds no map that a dense integer array replaces, no nested map that costs several lookups per access, and no repeated string compare that stands in for an enum on a hot path. The `_handlers` dictionary keyed by a pattern string is the right shape once you split the dispatch, as the P1 dispatch finding describes.
- **String building**: no finding. No loop appends to a string with `+=`, so no path is quadratic in the output size.
- **Deep copies**: no finding. The code contains no `copy.deepcopy` and no `json.loads(json.dumps(...))` round trip. `Event` is a frozen dataclass, and the middleware chain passes the same object through, so no middleware copies it.
- **Sorts**: no finding. No code sorts a collection only to read its minimum, its maximum, or its top k items.
- **Membership tests on a list inside a loop**: no finding on a hot path. The one linear scan, at `eventbus/bus.py:34`, runs at teardown.
- **N+1 query loops**: no finding inside this package. `SQLiteStorage.store` and `MemoryStorage.store` each take one event, so the N+1 shape lives in the caller. The P1 commit finding covers the fix.
- **Metrics and counters**: one finding only, the dead `_retry_counts` dictionary at `eventbus/middleware/retry.py:25`. The code holds no percentile computation per item and no debug timer on the serving path.