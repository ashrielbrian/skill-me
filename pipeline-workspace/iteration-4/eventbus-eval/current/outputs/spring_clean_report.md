# Codebase Audit Report

## Summary
This is an in-process async event bus library for Python (v1.3.0) providing topic-based pub/sub with middleware support (logging, retry, deduplication) and pluggable storage backends (in-memory and SQLite). The codebase is small and well-structured but contains several real issues ranging from an event-loop-blocking synchronous HTTP call in the webhook handler to sensitive data exposure through the logging middleware, an unbounded dead letter queue, and queue-draining semantics on shutdown that silently drop events. The most impactful finding is the synchronous `urllib` call inside an async handler, which will block the entire event loop during webhook delivery.

## Findings

### [Critical] Synchronous HTTP call blocks the async event loop
- **Category**: Performance
- **Location**: `eventbus/handlers/webhook.py`, lines 33-43
- **Problem**: The `WebhookHandler.__call__` method is an async handler invoked from the async event bus processing loop, but it uses synchronous `urllib.request.urlopen` to make HTTP POST requests. This blocks the entire asyncio event loop for up to `timeout` seconds (default 30), halting all event processing, not just the webhook delivery. Under any real load, this will freeze the bus entirely.
- **Evidence**:
  ```python
  async def __call__(self, event: Event) -> None:
      ...
      response = urllib.request.urlopen(req, timeout=self.timeout)
  ```
  The `urllib.request.urlopen` call is synchronous blocking I/O used inside an `async def` method. No `asyncio.to_thread`, `loop.run_in_executor`, or async HTTP library is used.
- **Suggested fix**: Replace `urllib.request` with an async HTTP client such as `aiohttp` or `httpx` (async mode). Alternatively, wrap the call in `asyncio.to_thread(urllib.request.urlopen, req, timeout=self.timeout)` as a minimal fix.
- **Effort**: Small (< 1 hour)

### [High] Logging middleware exposes sensitive payload data by default
- **Category**: Security
- **Location**: `eventbus/middleware/logging.py`, lines 12-30
- **Problem**: The `LoggingMiddleware` defaults to `include_payload=True`, meaning every event's full payload is serialized to JSON and written to the log. Events may carry PII, credentials, API keys, authentication tokens, or other sensitive data in their payloads. This creates a data leak risk in any environment where logs are aggregated, shipped to external services, or persisted.
- **Evidence**:
  ```python
  def __init__(self, level: int = logging.INFO, include_payload: bool = True):
      ...
  if self.include_payload:
      log_data["payload"] = event.payload
  logger.log(self.level, json.dumps(log_data))
  ```
  The default is `True` and there is no field-level filtering or redaction mechanism.
- **Suggested fix**: Change the default to `include_payload=False`. Better yet, add a field-redaction or allowlist mechanism so operators can control which payload fields appear in logs (e.g., a `redact_fields` parameter that replaces values with `"[REDACTED]"`).
- **Effort**: Small (< 1 hour)

### [High] Unbounded dead letter list grows without limit
- **Category**: Performance
- **Location**: `eventbus/bus.py`, lines 24 and 94-95
- **Problem**: Every event that is published but has no matching subscriber is appended to `self._dead_letter`, a plain Python list with no size cap. In a long-running process with misconfigured subscriptions or a period where subscribers are not yet registered, this list will grow indefinitely, consuming unbounded memory and eventually causing an OOM crash.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []
  ...
  if not dispatched:
      self._dead_letter.append(processed_event)
  ```
  No `maxlen`, no periodic cleanup, no eviction policy.
- **Suggested fix**: Replace with a bounded `collections.deque(maxlen=N)` or add a configurable max size with eviction (e.g., drop oldest). Also consider exposing a method to consume/drain dead letters so operators can process them.
- **Effort**: Small (< 1 hour)

### [High] Events in the queue are silently dropped on stop()
- **Category**: Correctness
- **Location**: `eventbus/bus.py`, lines 53-56
- **Problem**: When `stop()` is called, it sets `self._running = False`, which causes the `_process_loop` to exit on the next iteration. Any events already sitting in the queue are silently abandoned. This means events that were successfully published (the `publish()` call returned without error) are lost without any notification to the caller or any logging. This violates the at-least-once delivery expectation of an event bus.
- **Evidence**:
  ```python
  async def stop(self) -> None:
      """Stop processing events."""
      self._running = False
      # Does not drain the queue
  ```
  The `_process_loop` checks `while self._running:` and exits immediately when the flag is false, leaving queued events unprocessed.
- **Suggested fix**: Before setting `_running = False`, drain remaining events from the queue and process them (or at minimum, log a warning with the count of dropped events). A `drain_timeout` parameter would allow callers to set a deadline for graceful shutdown.
- **Effort**: Medium (hours)

### [Medium] Middleware error handling continues with original event state
- **Category**: Error Handling
- **Location**: `eventbus/bus.py`, lines 67-78
- **Problem**: When a middleware raises an exception, the `except` block logs a warning and then `break`s out of the middleware loop. However, `processed_event` still holds the value from before the failing middleware ran (or from the last successful middleware). The code then continues to dispatch this partially-processed event to handlers. This means if middleware A transforms an event and middleware B fails, handlers receive the output of A but not B, which may be an inconsistent or invalid state. The semantics are unclear and surprising.
- **Evidence**:
  ```python
  processed_event = event
  for mw in self._middleware:
      try:
          processed_event = await mw(processed_event)
          if processed_event is None:
              break
      except Exception as e:
          logger.warning(f"Middleware error: {e}")
          break
  ```
  After `break`, `processed_event` still holds the last successful result and the event proceeds to dispatch.
- **Suggested fix**: Either (a) skip the event entirely on middleware failure (set `processed_event = None` before `break`) or (b) route it to the dead letter queue with error context, or (c) document the current behavior explicitly and let operators decide via configuration. Option (a) is safest.
- **Effort**: Small (< 1 hour)

### [Medium] Fire-and-forget task in start() -- task reference is lost
- **Category**: Correctness
- **Location**: `eventbus/bus.py`, line 51
- **Problem**: `asyncio.create_task(self._process_loop())` creates a background task but does not store a reference to it. If the task raises an unhandled exception, it will be silently garbage-collected and the "Task exception was never retrieved" warning will appear. More importantly, the bus has no way to `await` the task during shutdown or detect if the processing loop died unexpectedly.
- **Evidence**:
  ```python
  async def start(self) -> None:
      self._running = True
      asyncio.create_task(self._process_loop())
  ```
  No `self._task = ...` assignment, no exception callback, no monitoring.
- **Suggested fix**: Store the task reference (`self._task = asyncio.create_task(...)`) and await it in `stop()`. Add an exception callback or wrap the loop body with a top-level try/except that sets `self._running = False` and logs the failure.
- **Effort**: Small (< 1 hour)

### [Medium] SQLiteStorage commits after every single insert
- **Category**: Performance
- **Location**: `eventbus/storage/sqlite.py`, line 45
- **Problem**: Every call to `store()` issues a separate `await self._db.commit()`. Under high event throughput, this means one fsync per event, which is extremely slow for SQLite (typically limited to ~50-100 fsyncs/second on spinning disk). This will become a severe bottleneck under load.
- **Evidence**:
  ```python
  async def store(self, event: Event) -> None:
      ...
      await self._db.execute("INSERT OR REPLACE INTO events ...")
      await self._db.commit()
  ```
- **Suggested fix**: Implement batched writes -- accumulate events and commit periodically (e.g., every N events or every T seconds), or use WAL mode (`PRAGMA journal_mode=WAL`) for better concurrent write performance. At minimum, offer a `batch_store()` method.
- **Effort**: Medium (hours)

### [Medium] SQLiteStorage lacks async context manager support, risking connection leaks
- **Category**: Correctness
- **Location**: `eventbus/storage/sqlite.py`, lines 73-82
- **Problem**: `SQLiteStorage` requires manual calls to `initialize()` and `close()`, but does not implement `__aenter__`/`__aexit__`. If `close()` is never called (e.g., due to an exception in the calling code), the SQLite connection leaks. This is a common pattern in Python async code and its absence is a correctness risk.
- **Evidence**:
  ```python
  async def close(self) -> None:
      if self._db:
          await self._db.close()
          self._db = None
  # No __aenter__ / __aexit__ defined
  ```
- **Suggested fix**: Add `__aenter__` (calls `initialize()`, returns `self`) and `__aexit__` (calls `close()`). This allows `async with SQLiteStorage(...) as storage:` usage.
- **Effort**: Small (< 1 hour)

### [Medium] MemoryStorage._by_topic lists grow without bound
- **Category**: Performance
- **Location**: `eventbus/storage/memory.py`, lines 14 and 19-21
- **Problem**: While `self._events` uses a bounded `deque(maxlen=max_events)`, the `self._by_topic` dictionary maps each topic to a plain `list` that grows without limit. Over time, for frequently-used topics, these lists accumulate all events ever stored, consuming unbounded memory. The eviction from the main deque does not trigger eviction from `_by_topic`.
- **Evidence**:
  ```python
  self._events: deque[Event] = deque(maxlen=max_events)
  self._by_topic: dict[str, list[Event]] = {}
  ...
  self._by_topic[event.topic].append(event)
  ```
  Events evicted from `_events` remain referenced in `_by_topic`, preventing garbage collection and growing memory usage indefinitely.
- **Suggested fix**: Use bounded deques for per-topic storage as well, or synchronize eviction: when an event is evicted from the main deque, also remove it from the per-topic list. The simplest fix is `self._by_topic[topic] = deque(maxlen=max_events_per_topic)`.
- **Effort**: Small (< 1 hour)

### [Medium] Deduplication cleanup is O(n) per event
- **Category**: Performance
- **Location**: `eventbus/middleware/dedup.py`, lines 24-33
- **Problem**: `_cleanup_expired()` is called on every incoming event and iterates the entire `_seen` dictionary to find expired entries. With `max_seen=10000`, this is O(10000) per event. Under high throughput, this adds significant overhead to every event processed through the dedup middleware.
- **Evidence**:
  ```python
  def _cleanup_expired(self):
      now = time.time()
      expired = [k for k, ts in self._seen.items()
                 if now - ts > self.window_seconds]
      for k in expired:
          del self._seen[k]
  ```
- **Suggested fix**: Use an `OrderedDict` or a `deque` of `(timestamp, event_id)` tuples to enable O(1) amortized cleanup by popping from the front until a non-expired entry is found. Alternatively, only run cleanup every N events instead of every event.
- **Effort**: Small (< 1 hour)

### [Low] RetryMiddleware.with_retry is never called by the bus
- **Category**: Maintainability
- **Location**: `eventbus/middleware/retry.py`, lines 28-41; `eventbus/bus.py`, lines 85-92
- **Problem**: The `RetryMiddleware` has a `with_retry` method designed to wrap handler dispatch with exponential backoff, but the `EventBus._process_loop` never calls it. The middleware's `__call__` method merely records the event_id and passes the event through unchanged. The retry logic is implemented but entirely dead -- handlers are called directly in the bus without any retry wrapping.
- **Evidence**:
  ```python
  # RetryMiddleware.__call__ just tracks and passes through
  async def __call__(self, event: Event) -> Event:
      self._retry_counts[event.event_id] = 0
      return event

  # Bus dispatches handlers directly without retry
  for handler in handlers:
      try:
          await handler(processed_event)
      except Exception as e:
          logger.error(f"Handler error for {pattern}: {e}")
  ```
  The `with_retry` method exists but is never invoked anywhere.
- **Suggested fix**: Either integrate `with_retry` into the bus's handler dispatch loop (detecting when `RetryMiddleware` is registered and wrapping handler calls accordingly), or redesign the retry middleware as a handler decorator rather than a middleware. The current architecture is misleading since users adding `RetryMiddleware` would expect retry behavior but get none.
- **Effort**: Medium (hours)

### [Low] Mutable default argument in WebhookHandler.__init__
- **Category**: Correctness
- **Location**: `eventbus/handlers/webhook.py`, line 18
- **Problem**: The `headers` parameter defaults to `None` and is handled with `headers or {...}`, which is the correct Python idiom. However, using `dict[str, str] = None` in the type hint without `Optional` is inconsistent with the type annotation (should be `Optional[dict[str, str]]` or `dict[str, str] | None`). This is a minor type-correctness issue that could confuse type checkers and IDE tooling.
- **Evidence**:
  ```python
  def __init__(self, url: str, headers: dict[str, str] = None,
               timeout: int = 30):
  ```
- **Suggested fix**: Change to `headers: dict[str, str] | None = None`.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **SQL Injection**: The SQLite storage backend correctly uses parameterized queries throughout. No string-formatted SQL was found.
- **Hardcoded Secrets**: No API keys, passwords, or credentials are hardcoded in the source. The `auth_token` in `create_webhook_handler` is passed as a parameter, not embedded.
- **Circular Dependencies**: The module dependency graph is clean and acyclic (event <- bus, event <- middleware/*, event <- storage/*, event <- handlers/*).
- **Input Validation on Event Creation**: The `Event` dataclass does not validate its inputs (e.g., empty topic strings), but given this is a library for in-process use rather than a public-facing API, this is an acceptable tradeoff.
- **Dependency Vulnerabilities**: The dependencies (`aiosqlite>=0.19.0`, `pydantic>=2.0.0`) are recent versions with no known critical vulnerabilities at time of review. Note that `pydantic` is declared as a dependency but is not actually imported anywhere in the codebase, suggesting it is unused.
