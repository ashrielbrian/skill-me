# Codebase Audit Report

## Summary

`eventbus` is a small Python 3.11+ library (version 1.3.0). It gives an in-process async publish/subscribe bus, two storage backends (SQLite and memory), three middleware classes, and one webhook handler. The code is clean and readable, but the runtime behavior has serious defects: the event loop of the bus can stop without a signal, `stop()` discards queued events, and `RetryMiddleware` gives no retry at all. Test coverage is 3 test functions for `bus.py` only, and no test covers the storage, the middleware, or the handler.

## Findings

### [Critical] An exception in the loop that dispatches events stops the bus without a signal

- **Category**: Correctness
- **Location**: `eventbus/bus.py:48-51`, `eventbus/bus.py:84-95`, `eventbus/bus.py:42-46`
- **Problem**: The loop at line 85 iterates `self._handlers` outside any `try` block. A handler that calls `subscribe()` adds a key to the dictionary. Python then raises `RuntimeError: dictionary changed size during iteration`. The exception leaves `_process_loop` and ends the task. Nothing sets `_running = False` and nothing logs the exception, because `start()` discards the task object. After this, `status()` still reports `"running": True` and `publish()` still accepts events. The events go into the queue and nobody delivers them. When the queue holds 10000 items, every call to `publish()` blocks forever. The operator sees a healthy bus and total event loss at the same time.
- **Evidence**:
  ```python
  asyncio.create_task(self._process_loop())
  ...
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
  ```
- **Suggested fix**: Keep the task object in `self._task`. Add a done callback that logs the exception. Wrap the body of the loop in a `try`/`except Exception` block. Iterate a copy of the handler map: `list(self._handlers.items())`.
- **Effort**: Small (< 1 hour)

### [High] The bus delivers an event after a middleware raises an exception

- **Category**: Error Handling
- **Location**: `eventbus/bus.py:68-81`
- **Problem**: The chain of middleware fails open. When a middleware raises, the bus logs a warning, breaks out of the chain, and then delivers the event. The bus also skips every middleware after the one that failed. A filter or a redaction step that fails becomes a no-op. Example: `LoggingMiddleware` calls `json.dumps` on a payload that holds `bytes`. `json.dumps` raises `TypeError`. The bus skips `DeduplicationMiddleware` and delivers the duplicate event.
- **Evidence**:
  ```python
  except Exception as e:
      logger.warning(f"Middleware error: {e}")
      break
  ```
- **Suggested fix**: Set `processed_event = None` in the `except` block, so the bus drops the event. Add a per-middleware flag if some middleware must fail open.
- **Effort**: Small (< 1 hour)

### [High] `stop()` discards every queued event

- **Category**: Correctness
- **Location**: `eventbus/bus.py:53-55`, `eventbus/bus.py:58-64`
- **Problem**: `stop()` sets one flag and returns. It does not drain the queue and it does not wait for the loop to exit. Every event in the queue is lost at shutdown. `stop()` also returns before the loop stops, so the loop can deliver one more event after `stop()` returns. This makes a clean shutdown impossible and makes tests non-deterministic. `start()` has no guard either, so two calls create two loops.
- **Evidence**:
  ```python
  async def stop(self) -> None:
      """Stop processing events."""
      self._running = False
  ```
- **Suggested fix**: Make `stop()` await `self._queue.join()` or drain the queue. Then await the task with a timeout. Add a guard in `start()` that returns if `self._running` is already `True`.
- **Effort**: Medium (hours)

### [High] `RetryMiddleware` gives no retry and leaks memory

- **Category**: Correctness
- **Location**: `eventbus/middleware/retry.py:22-26`, `eventbus/middleware/retry.py:28-41`, `eventbus/bus.py:88-92`
- **Problem**: No code calls `with_retry`. A search of the package finds the method definition only. `EventBus._process_loop` calls each handler directly. A user who registers `RetryMiddleware` with `bus.use()` gets no retry, and the bus reports no warning. A failed handler never runs again. The `__call__` method also writes one entry per event into `self._retry_counts` and never removes an entry. No code reads this dictionary. The dictionary grows for the life of the process.
- **Evidence**:
  ```python
  async def __call__(self, event: Event) -> Event:
      """Pass through — retry logic is applied at handler dispatch time."""
      # Track that this event entered the retry middleware
      self._retry_counts[event.event_id] = 0
      return event
  ```
- **Suggested fix**: Make the bus call `with_retry` at the point where it dispatches to a handler. Delete `self._retry_counts`, because no code reads it. Add a test that proves a failed handler runs again.
- **Effort**: Medium (hours)

### [High] `LoggingMiddleware` writes the full event payload to the log by default

- **Category**: Security
- **Location**: `eventbus/middleware/logging.py:12`, `eventbus/middleware/logging.py:24-30`
- **Problem**: The default value of `include_payload` is `True`. The middleware writes the complete payload of every event to the log. An event payload often holds personal data, an API key, or a password. The log becomes a copy of all data on the bus. Log files and log aggregation systems usually have a wider access group than the application data.
- **Evidence**:
  ```python
  def __init__(self, level: int = logging.INFO, include_payload: bool = True):
  ...
  if self.include_payload:
      log_data["payload"] = event.payload
  ```
- **Suggested fix**: Change the default of `include_payload` to `False`. Add a `redact_keys` parameter for the fields that the middleware must mask. Log the payload size and the key names instead of the values.
- **Effort**: Small (< 1 hour)

### [High] `WebhookHandler` blocks the event loop with synchronous HTTP

- **Category**: Performance
- **Location**: `eventbus/handlers/webhook.py:42-48`, `eventbus/handlers/webhook.py:19`
- **Problem**: The handler is `async`, but it calls `urllib.request.urlopen`, which is synchronous. The call blocks the whole asyncio event loop. The default timeout is 30 seconds. One slow webhook endpoint stops every event on the bus and every other task in the process for up to 30 seconds. The bus has one `_process_loop`, so there is no parallel path around the block.
- **Evidence**:
  ```python
  response = urllib.request.urlopen(req, timeout=self.timeout)
  ```
- **Suggested fix**: Replace `urllib` with `aiohttp` or `httpx.AsyncClient`. Add both libraries to the dependencies. As a short-term step, wrap the call in `asyncio.to_thread`.
- **Effort**: Medium (hours)

### [High] `MemoryStorage` has no limit on the per-topic index

- **Category**: Performance
- **Location**: `eventbus/storage/memory.py:12-21`
- **Problem**: The `_events` deque has the limit `max_events`, but the `_by_topic` dictionary has no limit. Each call to `store()` appends the event to a plain list in `_by_topic`. Nothing trims these lists. An event that the deque drops stays in `_by_topic` and keeps its memory. The dictionary also gets one new key for each new topic. In a long-running process the memory grows without a limit, and the process gets an out-of-memory error. The class docstring promises "a configurable max size", so the behavior is a surprise.
- **Evidence**:
  ```python
  self._events: deque[Event] = deque(maxlen=max_events)
  self._by_topic: dict[str, list[Event]] = {}
  ...
  self._by_topic[event.topic].append(event)
  ```
- **Suggested fix**: Make each value in `_by_topic` a `deque` with a `maxlen`. Add a limit on the number of topics. Alternatively, build the per-topic result from `_events` at query time.
- **Effort**: Small (< 1 hour)

### [Medium] The dead-letter list has no size limit

- **Category**: Performance
- **Location**: `eventbus/bus.py:24`, `eventbus/bus.py:94-95`
- **Problem**: The bus appends an event to `self._dead_letter` when no handler accepts it. No code removes an entry and no code reads the list, except `status()`, which reads only the length. A wrong pattern in a subscription, or a handler that raises for every event, sends every event to this list. The memory grows for the life of the process.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []  # BUG [PERFORMANCE]: Unbounded list grows forever
  ...
  if not dispatched:
      self._dead_letter.append(processed_event)
  ```
- **Suggested fix**: Change `_dead_letter` to a `collections.deque` with a `maxlen`. Add a counter for the events that the bus drops. Add a public method that reads and clears the list.
- **Effort**: Small (< 1 hour)

### [Medium] An HTTP redirect drops the webhook body and sends the auth token to a new host

- **Category**: Security
- **Location**: `eventbus/handlers/webhook.py:33-45`, `eventbus/handlers/webhook.py:52-57`
- **Problem**: `urllib.request.urlopen` follows a redirect by default. For a POST that gets status 301, 302, or 303, `HTTPRedirectHandler` builds a new `GET` request. It drops the body but keeps all headers except `content-length` and `content-type`. Two failures follow. First, the event data never reaches the target, and `urlopen` returns status 200, so the handler reports success. Second, `create_webhook_handler` puts a bearer token in the `Authorization` header, and the handler sends that token to the new host. A compromised or hostile webhook endpoint can steal the token with one redirect.
- **Evidence**:
  ```python
  headers["Authorization"] = f"Bearer {auth_token}"
  return WebhookHandler(url=url, headers=headers)
  ```
- **Suggested fix**: Use an HTTP client that does not follow a redirect, or set the redirect limit to zero. Treat a 3xx status as a delivery failure. Never send the `Authorization` header to a host that is not the configured host.
- **Effort**: Small (< 1 hour)

### [Medium] `DeduplicationMiddleware` cannot detect a duplicate event

- **Category**: Correctness
- **Location**: `eventbus/middleware/dedup.py:6-22`, `eventbus/event.py:15`
- **Problem**: The middleware compares `event.event_id`. The default factory of `event_id` is `uuid.uuid4()`, so each new `Event` object gets a new random identifier. Two events with the same topic and the same payload have different identifiers, and both pass the filter. The middleware stops an event only when the caller sends the identical `Event` object twice, or when the caller sets the same `event_id` by hand. The class docstring says "Filters duplicate events within a time window", which does not match the behavior.
- **Evidence**:
  ```python
  if event.event_id in self._seen:
      return None  # Duplicate — filter it
  ```
- **Suggested fix**: Add a `key_fn` parameter with a default that hashes the topic and the payload. Update the docstring to state that the caller must supply a stable `event_id`.
- **Effort**: Small (< 1 hour)

### [Medium] The two storage backends return `get_by_topic` results in opposite order

- **Category**: Correctness
- **Location**: `eventbus/storage/sqlite.py:54-58`, `eventbus/storage/memory.py:23-26`
- **Problem**: The two classes have the same method name and the same signature, so a user treats them as interchangeable. `SQLiteStorage` sorts with `ORDER BY timestamp DESC`, so it returns the newest event first. `MemoryStorage` returns the slice `events[-limit:]`, so it returns the oldest event first. A switch from the memory backend to the SQLite backend reverses the order of every result list. No test and no docstring records the intended order.
- **Evidence**:
  ```python
  "FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
  ```
  ```python
  return events[-limit:]
  ```
- **Suggested fix**: Pick one order and write it in the docstring of both methods. Reverse the result of one backend to match. Add a shared test that runs against both backends.
- **Effort**: Small (< 1 hour)

### [Medium] `SQLiteStorage.store` commits once per event and can raise `TypeError`

- **Category**: Performance
- **Location**: `eventbus/storage/sqlite.py:33-45`
- **Problem**: The method has two defects. First, it calls `commit()` after each single insert. Each commit forces a disk sync, so the throughput drops to a few hundred events per second. Second, `json.dumps(event.payload)` raises `TypeError` for a payload that holds `bytes`, a `datetime`, or a custom object. No code catches this error. The exception leaves `store()`, and the caller loses the event. Inside the bus, the dispatch code catches the exception and only logs it, so the event disappears without a clear record.
- **Evidence**:
  ```python
  (event.event_id, event.topic, json.dumps(event.payload),
   event.timestamp, event.source)
  )
  await self._db.commit()  # BUG [PERFORMANCE]: Commits after every single insert
  ```
- **Suggested fix**: Add a `store_many` method that inserts a batch in one transaction. Set `PRAGMA journal_mode=WAL` in `initialize()`. Wrap `json.dumps` in a `try` block and raise a clear `ValueError` with the topic and the event identifier.
- **Effort**: Medium (hours)

### [Medium] The cleanup of the deduplication cache scans every entry for each event

- **Category**: Performance
- **Location**: `eventbus/middleware/dedup.py:14-33`
- **Problem**: `__call__` calls `_cleanup_expired()` for every event. That method builds a list comprehension over the whole `_seen` dictionary. The default `max_seen` is 10000, so the middleware does up to 10000 comparisons for each event. The cost is O(n) per event and O(n²) for a burst of n events. The work is also wasted, because most entries are not expired.
- **Evidence**:
  ```python
  expired = [k for k, ts in self._seen.items()
             if now - ts > self.window_seconds]
  ```
- **Suggested fix**: Hold the identifiers in a `collections.OrderedDict` in time order. Remove entries from the front until the first entry is inside the window. This gives amortized O(1) cleanup.
- **Effort**: Small (< 1 hour)

### [Medium] The unsubscribe function raises an error and corrupts dispatch

- **Category**: Correctness
- **Location**: `eventbus/bus.py:26-36`, `eventbus/bus.py:87`
- **Problem**: The closure calls `list.remove`. A second call raises `ValueError: list.remove(x): x not in list`, because the handler is no longer in the list. Callers expect an unsubscribe function to be idempotent. A worse case happens during dispatch. The loop at line 87 iterates the same list object that `remove` changes. A handler that unsubscribes itself makes the loop skip the next handler for that pattern. That handler never sees the event.
- **Evidence**:
  ```python
  def unsubscribe():
      self._handlers[pattern].remove(handler)
  ```
- **Suggested fix**: Guard the removal with a check, or catch `ValueError` and return. Iterate a copy of the handler list at line 87: `for handler in list(handlers):`.
- **Effort**: Small (< 1 hour)

### [Medium] Tests cover only `bus.py` and depend on fixed sleep calls

- **Category**: Maintainability
- **Location**: `tests/test_bus.py:1-52`
- **Problem**: The suite has 3 test functions and all of them test `EventBus`. No test covers `SQLiteStorage`, `MemoryStorage`, `LoggingMiddleware`, `RetryMiddleware`, `DeduplicationMiddleware`, or `WebhookHandler`. This absence is the reason that the no-op `RetryMiddleware` reached version 1.3.0. Each test also waits with `await asyncio.sleep(0.2)`. A slow or loaded CI machine makes these tests fail at random. The repository holds no CI configuration file and no README.
- **Evidence**:
  ```python
  await bus.publish(Event(topic="test.topic", payload={"key": "value"}))
  await asyncio.sleep(0.2)
  await bus.stop()
  ```
- **Suggested fix**: Add a test module for each package: `storage`, `middleware`, and `handlers`. Replace each fixed sleep with an `asyncio.Event` that the handler sets. Add a CI workflow that runs `pytest`.
- **Effort**: Medium (hours)

### [Low] `SQLiteStorage` has no async context manager and leaks the connection

- **Category**: Error Handling
- **Location**: `eventbus/storage/sqlite.py:16-18`, `eventbus/storage/sqlite.py:73-81`
- **Problem**: The class has no `__aenter__` and no `__aexit__`, so `async with SQLiteStorage(...)` fails. A caller must remember `close()` in a `finally` block. If an exception happens first, the SQLite connection and its background thread stay open. A second call to `initialize()` also overwrites `self._db` and leaks the first connection.
- **Evidence**:
  ```python
  async def close(self) -> None:
      """Close the database connection."""
      if self._db:
  ```
- **Suggested fix**: Add `__aenter__` that calls `initialize()` and `__aexit__` that calls `close()`. Make `initialize()` return at once if `self._db` is not `None`.
- **Effort**: Small (< 1 hour)

### [Low] `Event` is frozen, but the payload dictionary stays mutable

- **Category**: Correctness
- **Location**: `eventbus/event.py:10-16`
- **Problem**: The docstring says "An immutable event with metadata", and the decorator sets `frozen=True`. The freeze applies to the attribute names only. The `payload` dictionary is still mutable. A handler can write `event.payload["x"] = 1`, and every later handler and every stored copy sees the change. The frozen dataclass also generates `__hash__` over all fields. A call to `hash(event)` raises `TypeError: unhashable type: 'dict'`, so an `Event` cannot go into a `set` or serve as a dictionary key.
- **Evidence**:
  ```python
  @dataclass(frozen=True)
  class Event:
      """An immutable event with metadata."""
      topic: str
      payload: dict[str, Any]
  ```
- **Suggested fix**: Use `types.MappingProxyType` for the payload, or copy the payload in `__post_init__`. Set `eq=False` or define `__hash__` from `event_id` only. Correct the docstring.
- **Effort**: Small (< 1 hour)

### [Low] The bus never uses a storage backend, but the description promises persistence

- **Category**: Maintainability
- **Location**: `pyproject.toml:4`, `eventbus/__init__.py:1`, `eventbus/bus.py:1-105`
- **Problem**: The project description says "In-process event bus with persistent storage". `EventBus` never imports `eventbus.storage` and never calls `store()`. Every published event lives in an in-memory `asyncio.Queue` only. A process restart loses all of them. A user reads the description, adds `SQLiteStorage`, and still gets no persistence unless the user writes the wiring by hand. No README and no docstring explains this step.
- **Evidence**:
  ```python
  """eventbus - In-process event bus with persistent storage."""
  ```
- **Suggested fix**: Add an optional `storage` parameter to `EventBus.__init__`. Call `await self._storage.store(event)` in `publish()`. As a smaller step, write the manual wiring in a README.
- **Effort**: Medium (hours)

## Issues Not Found

- **SQL injection**: `SQLiteStorage` uses parameterized queries in every method. The `topic` and `limit` values pass as bound parameters, not as string text. The comment at `eventbus/storage/sqlite.py:51` warns about injection, but the code is correct.
- **Hardcoded secrets**: No API key, password, or token exists in the source. `create_webhook_handler` takes the token as a parameter.
- **Wrong eviction order in the deduplication cache**: The comment at `eventbus/middleware/dedup.py:35` claims that the code drops the newest entries. The claim is wrong. A Python dictionary keeps insertion order, and a duplicate event returns before the code re-inserts the key. Therefore `list(self._seen.keys())[:excess]` removes the oldest entries, which is correct.
- **Retry backoff math**: `RetryMiddleware.with_retry` computes `self.base_delay * (2 ** attempt)` and runs `max_retries + 1` attempts. The math and the loop bounds are correct. The defect is that no code calls the method.
- **Pattern match logic**: `Event.matches` handles `*`, a `.*` suffix, and an exact topic correctly. It does not support a wildcard in the middle of a pattern, but no code and no test needs one.
- **Dependency vulnerabilities**: The project declares only `aiosqlite>=0.19.0` and `pydantic>=2.0.0`. No lock file exists, so no version pin can be checked. Note that no module imports `pydantic`; the dependency is unused.
- **Circular dependencies**: The import graph is a tree. Each subpackage imports `..event` only.