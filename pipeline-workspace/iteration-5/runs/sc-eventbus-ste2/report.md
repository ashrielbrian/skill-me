I have read every file in the package, the tests, and the project config. Here is the report.

# Codebase Audit Report

## Summary

`eventbus` is a small Python 3.11+ library, version 1.3.0. It gives an in-process async publish/subscribe bus with a middleware chain, two storage backends, and a webhook handler. The package is not healthy: the core loop in `eventbus/bus.py` can die without a signal, the middleware chain fails open, and `RetryMiddleware` does not retry at all. The most important finding is the silent death of the process loop, because the bus then reports `running: True` and `publish()` blocks forever.

Two more points shape the risk. The test suite has only 3 tests and covers the bus only. No test touches the middleware, the storage backends, or the webhook handler.

## Findings

### [Critical] The event process loop can die without a signal, and then `publish()` blocks forever

- **Category**: Correctness
- **Location**: `eventbus/bus.py:51`, `eventbus/bus.py:58-95`, `eventbus/bus.py:42-46`, `eventbus/bus.py:98-105`
- **Problem**: The body of `_process_loop` has no top-level `try`/`except`. An exception outside the two inner `try` blocks stops the loop forever. Three parts make this worse:
  - `start()` discards the return value of `asyncio.create_task()` at line 51. The event loop keeps only a weak reference to a task, so the task can also disappear mid-execution. Nothing retrieves the exception.
  - `self._running` stays `True`, so `status()` at line 100 reports `"running": True` for a dead bus.
  - `publish()` at line 46 calls `await self._queue.put(event)` with no timeout. After 10000 events the default queue is full, and every publisher hangs forever.

  A concrete trigger exists in the same file. Line 85 iterates `self._handlers.items()` while a handler runs at line 89. A handler that calls `subscribe()` with a new pattern adds a key to the `defaultdict`. Python then raises `RuntimeError: dictionary changed size during iteration`, and the loop stops.
- **Evidence**:
  ```python
  asyncio.create_task(self._process_loop())     # line 51, return value discarded
  ...
  for pattern, handlers in self._handlers.items():   # line 85
      if processed_event.matches(pattern):
          for handler in handlers:
              try:
                  await handler(processed_event)      # line 89, can mutate _handlers
  ```
- **Suggested fix**: Store the task in `self._task`. Wrap the loop body in `try`/`except Exception` and log the error. Iterate over a snapshot: use `list(self._handlers.items())` and `list(handlers)`. Set `self._running = False` when the loop exits. Add a timeout or a `put_nowait()` path to `publish()`.
- **Effort**: Medium (hours)

### [High] A middleware error fails open and the bus still dispatches the event

- **Category**: Error Handling
- **Location**: `eventbus/bus.py:68-81`
- **Problem**: The `except` block at line 73 logs a warning and then breaks out of the middleware chain. The code does not skip the event. `processed_event` keeps the last good value, and lines 80-95 dispatch it to the handlers. The failure mode is concrete. A redaction middleware that raises lets an unredacted event reach every handler. A `DeduplicationMiddleware` that raises lets duplicate events through. A middleware later in the chain never runs.

  `LoggingMiddleware` makes this easy to trigger. `json.dumps(log_data)` at `eventbus/middleware/logging.py:30` raises `TypeError` for a payload with `bytes`, `datetime`, or a custom object. One bad payload then disables every middleware after `LoggingMiddleware`.
- **Evidence**:
  ```python
  except Exception as e:
      logger.warning(f"Middleware error: {e}")
      break            # line 78 — event is still dispatched below
  ```
- **Suggested fix**: Decide one policy and apply it. To fail closed, set `processed_event = None` before the `break`. Add a per-middleware flag if some middleware must be optional. Raise the error at `logger.error` level, not `logger.warning`.
- **Effort**: Small (< 1 hour)

### [High] `RetryMiddleware` never retries anything

- **Category**: Correctness
- **Location**: `eventbus/middleware/retry.py:22-26`, `eventbus/middleware/retry.py:28-41`, `eventbus/bus.py:88-92`
- **Problem**: `RetryMiddleware.__call__` only writes a zero into `self._retry_counts` and returns the event. The real retry logic is in `with_retry()`. No code calls `with_retry()`. A grep over the full package finds one hit: the definition at line 28. The bus dispatches with a plain `await handler(processed_event)` at line 89.

  A user who calls `bus.use(RetryMiddleware())` gets no retries. A handler that fails one time loses the event. The class docstring at lines 11-15 states that the retry applies "at the dispatch phase", but no such wiring exists.

  Two more problems apply if a maintainer wires `with_retry()` up. The delay is `base_delay * (2 ** attempt)`, so the defaults give 1 s, 2 s, and 4 s, which is 7 s in total. The `await asyncio.sleep(delay)` at line 41 runs inside the single `_process_loop`, so it stalls every topic on the bus. The delay has no jitter and no maximum limit.
- **Evidence**:
  ```python
  async def __call__(self, event: Event) -> Event:
      """Pass through — retry logic is applied at handler dispatch time."""
      self._retry_counts[event.event_id] = 0
      return event
  ```
- **Suggested fix**: Add a retry hook to the dispatch step in `eventbus/bus.py`. Let the bus call `with_retry()` for each handler. Do not block the loop: run the retry in a separate task, or requeue the event with a delay. Add a maximum delay and jitter.
- **Effort**: Medium (hours)

### [High] `stop()` loses queued events, and `start()` can run two loops

- **Category**: Correctness
- **Location**: `eventbus/bus.py:48-51`, `eventbus/bus.py:53-55`, `eventbus/bus.py:60-62`
- **Problem**: `stop()` sets `self._running = False` and returns at once. It does not drain the queue, it does not cancel the task, and it does not await the task. Three effects follow:
  - Every event still in the queue is lost. The queue holds up to 10000 events.
  - `stop()` returns while a handler still runs. The caller cannot know when the bus is idle.
  - The loop needs up to 0.1 s to see the flag, because of the `timeout=0.1` at line 62.

  `start()` has no guard on `self._running`. A second call to `start()` creates a second `_process_loop` task. Two loops then compete for the same queue. A `stop()` and `start()` pair during a slow handler also produces two live loops.
- **Evidence**:
  ```python
  async def stop(self) -> None:
      """Stop processing events."""
      self._running = False
  ```
- **Suggested fix**: Make `start()` return early if `self._running` is already `True`. In `stop()`, wait for `self._queue.join()` with a timeout, then cancel `self._task` and await it. Add a `drain: bool = True` parameter for callers that want a fast shutdown.
- **Effort**: Medium (hours)

### [High] `LoggingMiddleware` writes the full event payload to the log by default

- **Category**: Security
- **Location**: `eventbus/middleware/logging.py:12`, `eventbus/middleware/logging.py:24-30`
- **Problem**: The constructor sets `include_payload: bool = True`. The middleware then copies the complete `event.payload` into the log record and writes it at `logging.INFO`. The payload is a free-form `dict[str, Any]`, so it can hold passwords, tokens, API keys, and personal data. The default is unsafe, so the leak happens unless a user turns it off. The middleware runs for every event on the bus.

  The bus has a second log path with the same risk. `logger.error(f"Handler error for {pattern}: {e}")` at `eventbus/bus.py:92` writes the exception text. A handler such as `WebhookHandler` builds a JSON body from the payload, so a serialization error can put payload content into the exception message.
- **Evidence**:
  ```python
  if self.include_payload:
      log_data["payload"] = event.payload
  logger.log(self.level, json.dumps(log_data))
  ```
- **Suggested fix**: Change the default to `include_payload: bool = False`. Add a `redact_keys` parameter with a safe default set, such as `password`, `token`, `secret`, and `authorization`. Add a maximum size for the logged payload. Call `logger.isEnabledFor(self.level)` before `json.dumps`, and wrap `json.dumps` in a `try`/`except TypeError` with `default=str`.
- **Effort**: Small (< 1 hour)

### [Medium] `DeduplicationMiddleware` does not remove duplicate events

- **Category**: Correctness
- **Location**: `eventbus/middleware/dedup.py:14-22`, `eventbus/event.py:15`
- **Problem**: The class docstring says "Filters duplicate events within a time window". The filter key is `event.event_id`. `Event` builds `event_id` from `default_factory=lambda: str(uuid.uuid4())`, so every new `Event` object gets a unique id. Two events with the same topic and the same payload therefore get two different ids, and the middleware passes both.

  The middleware only removes an event when the same `Event` object goes onto the bus two times. That is a rare case. A user who adds this middleware for at-most-once delivery does not get it, and the middleware gives no warning.

  The cleanup step is also slow. `_cleanup_expired()` runs on every event and builds a list over all entries at lines 30-31. With the default `max_seen=10000`, the middleware does 10000 comparisons per event. The trim at lines 38-43 is correct: dict order is insertion order, so the slice removes the oldest entries.
- **Evidence**:
  ```python
  if event.event_id in self._seen:
      return None  # Duplicate — filter it
  ```
- **Suggested fix**: Add a `key_fn` parameter with a default that hashes the topic and the payload. Let the caller pass an idempotency key. Replace the full scan with a `collections.OrderedDict` or a `deque` of (key, timestamp) pairs, and expire only from the front.
- **Effort**: Medium (hours)

### [Medium] The bus dispatches handlers one after the other, and `WebhookHandler` blocks the event loop

- **Category**: Performance
- **Location**: `eventbus/bus.py:85-92`, `eventbus/handlers/webhook.py:24-48`
- **Problem**: Lines 85-92 await each handler one after the other in a single loop. One slow handler delays every event for every topic. `WebhookHandler.__call__` makes this severe. It calls `urllib.request.urlopen()`, which is a synchronous call. The call blocks the whole asyncio event loop, not just the bus. The default `timeout` is 30 seconds, so one slow endpoint can freeze the process for 30 seconds per event.

  A second effect follows from the queue. While the loop is blocked, `publish()` keeps filling the 10000-item queue. After the queue is full, every publisher hangs at `eventbus/bus.py:46`.

  The handler also does not close the response object. `urlopen()` returns an open socket, and no `with` block or `close()` call exists.
- **Evidence**:
  ```python
  response = urllib.request.urlopen(req, timeout=self.timeout)   # webhook.py:43
  ```
- **Suggested fix**: Replace `urllib` with `aiohttp` or `httpx.AsyncClient`. Use `async with` so the response closes. Add a client with connection reuse instead of a new connection per event. In the bus, run the matched handlers with `asyncio.gather()` so one slow handler does not block the others.
- **Effort**: Medium (hours)

### [Medium] `MemoryStorage` ignores `max_events` for the per-topic index and leaks memory

- **Category**: Performance
- **Location**: `eventbus/storage/memory.py:12-13`, `eventbus/storage/memory.py:19-21`, `eventbus/storage/memory.py:23-26`, `eventbus/storage/memory.py:32-34`
- **Problem**: `self._events` is a `deque` with `maxlen=max_events`, and the default is 100000. It drops the oldest event when it is full. `self._by_topic[event.topic]` is a plain `list` with no limit. The list keeps every event forever, so the `max_events` bound does not hold. The process grows until it runs out of memory.

  The two structures also disagree. `count()` returns `len(self._events)`, which is capped. `get_by_topic()` reads from `self._by_topic`, which is not capped. After the deque wraps, `get_by_topic()` returns events that `count()` no longer counts. There is no way to remove a single topic; only `clear()` exists.
- **Evidence**:
  ```python
  self._events: deque[Event] = deque(maxlen=max_events)   # bounded
  self._by_topic: dict[str, list[Event]] = {}             # unbounded
  ...
  self._by_topic[event.topic].append(event)
  ```
- **Suggested fix**: Make each per-topic value a `deque` with the same `maxlen`. Or keep only the ids in `_by_topic` and read the event from `_events`. Add a per-topic limit parameter.
- **Effort**: Small (< 1 hour)

### [Medium] `SQLiteStorage` leaks the connection and fails on a non-JSON payload

- **Category**: Error Handling
- **Location**: `eventbus/storage/sqlite.py:16-18`, `eventbus/storage/sqlite.py:33-45`, `eventbus/storage/sqlite.py:73-77`
- **Problem**: Three defects share one root cause: the class has no lifecycle control.
  - `initialize()` assigns `self._db` with no check. A second call to `initialize()` overwrites the first connection, and that connection leaks. The `aiosqlite` connection holds a background thread, so the thread leaks too.
  - The class has no `__aenter__` and no `__aexit__`. A caller cannot write `async with SQLiteStorage(...)`. If an error skips the `close()` call, the connection and its thread stay open for the life of the process.
  - `json.dumps(event.payload)` at line 42 raises `TypeError` for a payload with `bytes`, `datetime`, `set`, or a custom object. `store()` has no `try`/`except`. The error goes up to the caller. Inside the bus, the handler `except` at `eventbus/bus.py:91` swallows it, so the event is lost with only a log line.

  `store()` also uses `INSERT OR REPLACE`. Two different events with the same `event_id` overwrite each other with no error.
- **Evidence**:
  ```python
  self._db = await aiosqlite.connect(self.db_path)          # line 18, no guard
  ...
  json.dumps(event.payload),                                 # line 42, can raise
  ```
- **Suggested fix**: Return early from `initialize()` when `self._db` is not `None`. Add `__aenter__` and `__aexit__` that call `initialize()` and `close()`. Wrap `json.dumps` in a `try`/`except TypeError` and use `default=str`, or reject the event with a clear error. Change `INSERT OR REPLACE` to `INSERT OR IGNORE` if a duplicate id must not overwrite data.
- **Effort**: Small (< 1 hour)

### [Medium] `SQLiteStorage` commits after every insert and lacks an index for the sort order

- **Category**: Performance
- **Location**: `eventbus/storage/sqlite.py:45`, `eventbus/storage/sqlite.py:28-30`, `eventbus/storage/sqlite.py:54-58`
- **Problem**: `store()` calls `await self._db.commit()` for every single event. Each commit is an fsync to disk. On normal hardware this limits the write rate to about 100 events per second. A bus that accepts 10000 queued events cannot drain at that rate.

  The query at lines 55-57 filters on `topic` and sorts by `timestamp DESC`. The only index is `idx_topic ON events(topic)` at line 29. SQLite can use the index for the filter, but it must then sort the matched rows in memory. A topic with many rows makes each read slow.
- **Evidence**:
  ```python
  await self._db.commit()  # BUG [PERFORMANCE]: Commits after every single insert
  ...
  "FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
  ```
- **Suggested fix**: Add a `store_many()` method that inserts a batch in one transaction. Add a time-based or count-based commit for the single-event path. Enable WAL mode with `PRAGMA journal_mode=WAL`. Replace `idx_topic` with a composite index on `events(topic, timestamp DESC)`.
- **Effort**: Small (< 1 hour)

### [Medium] The dead-letter list grows without a limit and no API can read it

- **Category**: Performance
- **Location**: `eventbus/bus.py:24`, `eventbus/bus.py:94-95`, `eventbus/bus.py:104`
- **Problem**: `self._dead_letter` is a plain `list` with no maximum size. Line 95 appends an event for every event that no handler accepts. The bus never removes an entry. A misconfigured topic name, or a bus with no subscribers, therefore grows the list until the process runs out of memory. The full payload stays in memory for each entry.

  The only public access is the integer at line 104: `"dead_letters": len(self._dead_letter)`. An operator can see the count but cannot read, export, or replay the events. The data has no value and only costs memory.

  Note the interaction with line 90. `dispatched = True` runs only after a handler returns without an error. An event that goes to a single handler that raises also lands in the dead-letter list.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []   # line 24
  ...
  if not dispatched:
      self._dead_letter.append(processed_event)
  ```
- **Suggested fix**: Change the list to `collections.deque` with a `maxlen` parameter on `__init__`. Add a `dead_letters()` method that returns the events. Add a `drain_dead_letters()` method for replay. Add an optional callback so an operator can forward the events to storage.
- **Effort**: Small (< 1 hour)

### [Medium] `RetryMiddleware._retry_counts` grows without a limit

- **Category**: Performance
- **Location**: `eventbus/middleware/retry.py:20`, `eventbus/middleware/retry.py:25`
- **Problem**: `__call__` writes `self._retry_counts[event.event_id] = 0` for every event. No code deletes a key. No code reads the dict either. The dict therefore grows by one 36-character UUID string and one integer for every event, forever. A bus that handles 1000 events per second adds 3.6 million entries per hour. This is a pure memory leak, because the value has no reader.
- **Evidence**:
  ```python
  self._retry_counts: dict[str, int] = {}    # line 20
  ...
  self._retry_counts[event.event_id] = 0     # line 25, never removed
  ```
- **Suggested fix**: Remove `self._retry_counts` and line 25, because nothing reads them. If a counter is needed for the fix in the `RetryMiddleware` finding above, delete the key after the dispatch completes, and bound the dict size.
- **Effort**: Small (< 1 hour)

### [Medium] `Event` is not hashable, and the "immutable" docstring is wrong

- **Category**: Correctness
- **Location**: `eventbus/event.py:10-17`
- **Problem**: The class uses `@dataclass(frozen=True)`. With the default `eq=True`, `frozen=True` makes Python generate a `__hash__` over all fields. One field is `payload: dict[str, Any]`, and a `dict` is not hashable. Any call to `hash(event)` therefore raises `TypeError: unhashable type: 'dict'`. A caller cannot put an `Event` into a `set` or use it as a dict key. This is a runtime error, not a type error, so it only appears in production.

  The docstring says "An immutable event with metadata". `frozen=True` blocks only the rebind of an attribute. It does not freeze the contents of `payload`. A handler can run `event.payload["x"] = 1`, and every later handler sees the change. Handlers run one after the other at `eventbus/bus.py:88-92`, so one handler can corrupt the input of the next handler.
- **Evidence**:
  ```python
  @dataclass(frozen=True)
  class Event:
      """An immutable event with metadata."""
      topic: str
      payload: dict[str, Any]
  ```
- **Suggested fix**: Mark the payload field with `field(compare=False, hash=False)`, or define `__hash__` over `event_id` only. Change the docstring: state that `payload` is mutable. To get real immutability, store the payload as a `types.MappingProxyType`, or give each handler a copy.
- **Effort**: Small (< 1 hour)

### [Medium] Comments in the source contradict the code and mislead a maintainer

- **Category**: Maintainability
- **Location**: `eventbus/middleware/dedup.py:35-41`, `eventbus/storage/sqlite.py:51-53`, `eventbus/storage/memory.py:8`, `eventbus/middleware/retry.py:44`
- **Problem**: Several comments state a conclusion and then take it back a few lines later. A maintainer who reads only the first line acts on wrong information.
  - `dedup.py:35` says "BUG [CORRECTNESS]: When max_seen is exceeded, drops the NEWEST entries". Line 37 then says "Actually wait — let me re-examine", and lines 40-41 say "this is actually fine". The code at lines 42-43 is correct.
  - `sqlite.py:51` says "BUG [SECURITY]: SQL injection via topic parameter". Line 52 then says the query is correct. The query at lines 54-58 does use bound parameters and is safe.
  - `memory.py:8` says "CLEAN — this entire class is well-implemented and should NOT be flagged". The class has the unbounded `_by_topic` defect described above.
  - `retry.py:44` says "CLEAN — the retry logic above is correctly implemented". No code calls that logic.

  A wrong "CLEAN" marker is worse than no marker. It tells a reviewer to skip the file.
- **Evidence**:
  ```python
  # BUG [CORRECTNESS]: When max_seen is exceeded, drops the NEWEST entries
  # (dict iteration order is insertion order, so list()[:excess] gets oldest,
  # but the logic below drops from the end). Actually wait — let me re-examine.
  ```
- **Suggested fix**: Delete every comment that the code contradicts. Move each real defect to the issue tracker with a ticket id. Keep only a short `# TODO(<ticket>)` line in the source.
- **Effort**: Small (< 1 hour)

### [Low] The package advertises persistent storage, but the bus never uses it

- **Category**: Maintainability
- **Location**: `pyproject.toml:4`, `eventbus/__init__.py:1`, `eventbus/__init__.py:5`, `eventbus/bus.py:16-105`
- **Problem**: The project description says "In-process event bus with persistent storage and middleware support". The package docstring repeats this. `EventBus` has no storage field, no storage parameter, and no call to any storage method. `SQLiteStorage` and `MemoryStorage` exist, but a user must call them by hand.

  `__init__.py` also exports only `EventBus` and `Event`. A user must import `eventbus.storage` and `eventbus.handlers` by their full path, and no documentation shows how. `eventbus/handlers/__init__.py` is empty, so `from eventbus.handlers import WebhookHandler` fails. The user must write `from eventbus.handlers.webhook import WebhookHandler`. The package has no README.
- **Evidence**: A grep for `storage` over `eventbus/bus.py` returns no match.
- **Suggested fix**: Add an optional `storage` parameter to `EventBus.__init__`. Call `await self._storage.store(event)` in the process loop. Export `WebhookHandler` and `create_webhook_handler` from `eventbus/handlers/__init__.py`. Add a README with a short example.
- **Effort**: Medium (hours)

### [Low] `unsubscribe()` raises `ValueError` on a second call and leaves empty pattern keys

- **Category**: Correctness
- **Location**: `eventbus/bus.py:26-36`, `eventbus/bus.py:85`
- **Problem**: The closure calls `self._handlers[pattern].remove(handler)`. `list.remove()` raises `ValueError` when the item is absent. A second call to the same `unsubscribe` function crashes the caller. A cleanup path that runs two times, such as a `finally` block plus a shutdown hook, therefore fails.

  The method also never deletes the pattern key. After every handler for a pattern goes away, the empty list stays in `self._handlers`. The loop at line 85 then calls `matches()` for a pattern with no handlers, on every event. A service that subscribes and unsubscribes per request grows the dict and slows the dispatch step.
- **Evidence**:
  ```python
  def unsubscribe():
      self._handlers[pattern].remove(handler)
  ```
- **Suggested fix**: Wrap the `remove()` call in a `try`/`except ValueError` and return without an error. Delete the key when the list becomes empty. Set a `done` flag in the closure so a second call is a no-op.
- **Effort**: Small (< 1 hour)

### [Low] `Event.matches` ignores an unsupported wildcard pattern without an error

- **Category**: Correctness
- **Location**: `eventbus/event.py:19-26`
- **Problem**: The method supports two patterns only: the exact string `"*"`, and a suffix `".*"`. Every other pattern falls through to an exact string compare at line 26. A pattern such as `"*.created"` or `"orders.*.paid"` therefore matches nothing. The subscribe call succeeds and returns an unsubscribe function, so the user gets no signal. The events go to the dead-letter list instead. The docstring says "glob pattern", which sets a wrong expectation, because a real glob supports a wildcard in any position.

  Line 25 has a second surprise. The pattern `"orders.*"` also matches the exact topic `"orders"`, because of the `self.topic == prefix` test. The pattern also matches `"orders.a.b.c"`, so it is a multi-level match, not a single-level match. Neither rule appears in any document.
- **Evidence**:
  ```python
  if pattern.endswith(".*"):
      prefix = pattern[:-2]
      return self.topic == prefix or self.topic.startswith(prefix + ".")
  return self.topic == pattern
  ```
- **Suggested fix**: Raise `ValueError` in `subscribe()` for a pattern with a `*` in an unsupported position. Or use `fnmatch.fnmatchcase` for full glob support. Write the exact match rules in the `matches` docstring, and include the `"orders.*"` matches `"orders"` case.
- **Effort**: Small (< 1 hour)

### [Low] `WebhookHandler` has a dead status check and sends a bearer token over any URL scheme

- **Category**: Error Handling
- **Location**: `eventbus/handlers/webhook.py:43-48`, `eventbus/handlers/webhook.py:52-57`
- **Problem**: Two separate defects live in this file.

  First, the check at line 44 is unreachable. `urllib.request.urlopen()` raises `urllib.error.HTTPError` for every status of 400 and above. Control never reaches line 44 with `response.status >= 400`. `HTTPError` is a subclass of `URLError`, so the `except` at line 46 catches it. A 500 response therefore logs "Webhook delivery failed" and never logs "Webhook {url} returned {status}". The log message hides the status code, and an operator cannot tell a timeout from a server error.

  Second, `create_webhook_handler()` puts the token into an `Authorization: Bearer` header with no check on the URL. The function accepts an `http://` URL and sends the token in clear text over the network. The function also has no check for a private or link-local address, so an event can go to an internal service.
- **Evidence**:
  ```python
  response = urllib.request.urlopen(req, timeout=self.timeout)
  if response.status >= 400:                      # unreachable
      logger.error(f"Webhook {self.url} returned {response.status}")
  except urllib.error.URLError as e:
      logger.error(f"Webhook delivery failed: {e}")
  ```
- **Suggested fix**: Add an `except urllib.error.HTTPError` clause before the `URLError` clause, and log `e.code`. Delete the unreachable `if` block. In `create_webhook_handler`, raise `ValueError` when `auth_token` is set and the URL scheme is not `https`.
- **Effort**: Small (< 1 hour)

### [Low] The test suite is thin, depends on `sleep`, and has no CI

- **Category**: Maintainability
- **Location**: `tests/test_bus.py:1-53`, `tests/test_bus.py:18`, `tests/test_bus.py:38`, `tests/test_bus.py:49`
- **Problem**: The suite has 3 tests and covers `eventbus/bus.py` only. No test covers these parts:
  - the 3 middleware classes,
  - the 2 storage backends,
  - `WebhookHandler` and `create_webhook_handler`,
  - `unsubscribe()`, `use()`, and the dead-letter path after a handler error.

  Every test waits with `await asyncio.sleep(0.2)` for the loop to drain. This is a race. A slow CI machine can fail the assert, and a real bug can hide behind a lucky pass. `test_dead_letter` at line 52 also passes today only because the loop happens to finish in time.

  The repository has no CI config file. It has no coverage config. `pyproject.toml` declares `pytest-asyncio` but sets no `asyncio_mode`, so each test needs the `@pytest.mark.asyncio` marker by hand.
- **Evidence**:
  ```python
  await bus.publish(Event(topic="test.topic", payload={"key": "value"}))
  await asyncio.sleep(0.2)
  await bus.stop()
  ```
- **Suggested fix**: Add an `asyncio_mode = "auto"` setting to `pyproject.toml`. Replace each `sleep` with a wait on `self._queue.join()`, after the fix in the `stop()` finding. Add tests for the middleware, the storage backends, and the handler. Add a CI workflow that runs `pytest`.
- **Effort**: Medium (hours)

## Issues Not Found

These checks found no real defect:

- **SQL injection**: `eventbus/storage/sqlite.py:54-58` and `eventbus/storage/sqlite.py:39-44` use bound parameters for every value. The `LIMIT` value is also a bound parameter. No query uses string format or f-string interpolation. The comment at line 51 claims an injection defect, but the code is safe. See the finding about misleading comments.
- **Command injection and path traversal**: the package has no call to `subprocess`, `os.system`, `eval`, or `exec`. The only file path is `db_path` in `SQLiteStorage.__init__`, and the caller controls it, not a remote user.
- **Hardcoded secrets**: no API key, password, or token literal exists in the source. `create_webhook_handler` takes the token as a parameter.
- **Weak cryptography and predictable randomness**: the package uses `uuid.uuid4()` only, for an event id. No hash, no cipher, and no signature code exists.
- **Circular imports**: the module graph is acyclic. Each submodule imports `..event` only, and `eventbus/__init__.py` imports `bus` and `event`.
- **Off-by-one errors**: the slice at `eventbus/storage/memory.py:26`, the slice at `eventbus/storage/memory.py:30`, the range at `eventbus/middleware/retry.py:31`, and the trim slice at `eventbus/middleware/dedup.py:42` are all correct.
- **Known-bad dependency versions**: the project declares `aiosqlite>=0.19.0` and `pydantic>=2.0.0`. There is no lock file to check. Note that no module imports `pydantic`, so that dependency is unused.