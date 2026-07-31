I've read the full codebase (510 lines) and verified the cross-module claims and the relevant CPython `urllib` behavior. Here's the report.

# Codebase Audit Report

## Summary

`eventbus` is a ~500-line Python 3.11+ library providing an in-process async pub/sub bus (`EventBus`), two storage backends (SQLite via `aiosqlite`, in-memory), three middleware (logging, retry, dedup), and an HTTP webhook handler. Despite the `1.3.0` version and a description promising "persistent storage and middleware support," the project has no README, no CI, three timing-based tests covering only `bus.py`, and — verified by grep — **the storage backends and the retry logic are never wired into the bus at all**.

Overall health: the core dispatch loop is the weakest part. The most important finding is a causal chain in `bus.py` where a routine usage pattern (subscribing from inside a handler) kills the unsupervised processing task with no log output, after which `publish()` silently accepts events until the queue fills and then blocks forever — a total, invisible pipeline halt. Several modules also carry in-source `BUG [...]` annotations; some are accurate, one contradicts itself, one flags a correctly-parameterized query, and two classes marked `CLEAN` contain real defects. I audited on the merits rather than trusting those markers.

## Findings

### [CRITICAL] Subscribing during dispatch kills the processing loop, which then fails silently and deadlocks `publish()`
- **Category**: Correctness
- **Location**: `eventbus/bus.py:51`, `eventbus/bus.py:58-95`, `eventbus/bus.py:44-46`
- **Problem**: Three defects compose into a silent total outage. (1) `_process_loop` iterates `self._handlers.items()` while dispatching — if any handler calls `bus.subscribe()` for a *new* pattern (a normal pattern: a handler that lazily registers a child subscription), the dict changes size mid-iteration and Python raises `RuntimeError: dictionary changed size during iteration`. That raise happens *outside* the per-handler `try`, so it escapes the loop. (2) `start()` discards the `create_task` return value, so nothing ever retrieves the task's exception — the `RuntimeError` is never logged and never surfaces (the asyncio docs also warn an unreferenced task can vanish mid-execution). (3) `_running` is still `True`, so `publish()` passes its guard and `await self._queue.put(event)` succeeds until 10 000 events accumulate, then blocks **forever** with no timeout and no consumer. The application hangs with zero log lines pointing at the cause.
- **Evidence**:
  ```python
  # bus.py:51
  asyncio.create_task(self._process_loop())      # handle discarded; exceptions never retrieved

  # bus.py:85 — mutation during iteration is unguarded
  for pattern, handlers in self._handlers.items():
      if processed_event.matches(pattern):
          for handler in handlers:
              try: await handler(processed_event)   # handler may subscribe() → RuntimeError escapes here
              except Exception as e: logger.error(...)

  # bus.py:46 — no timeout, no backpressure signal
  await self._queue.put(event)
  ```
  The same iteration hazard applies to the inner `for handler in handlers` loop: an `unsubscribe()` called from within a handler mutates the list being iterated, silently skipping the next handler. `start()` is also not idempotent — calling it twice spawns two competing loops.
- **Suggested fix**: Iterate a snapshot (`list(self._handlers.items())`, and `list(handlers)`); store the task on `self._task` and attach a done-callback that logs exceptions and clears `_running`; wrap the whole loop body in a `try/except Exception` that logs and continues so one bad event can't kill the worker; give `publish()` a bounded `asyncio.wait_for` on `put` (or use `put_nowait` and raise a `QueueFull` the caller can handle) so backpressure is visible instead of a hang.
- **Effort**: Small

### [HIGH] Synchronous `urllib` in an async handler blocks the event loop and serializes the entire bus
- **Category**: Performance
- **Location**: `eventbus/handlers/webhook.py:43`; amplified by `eventbus/bus.py:85-92`
- **Problem**: `urllib.request.urlopen(req, timeout=self.timeout)` is a blocking call inside `async def __call__`. With the default `timeout=30`, one unresponsive webhook endpoint freezes the *entire process* event loop for 30 s — not just the bus, but every other coroutine in the host application (HTTP server, DB pool, health checks). Because `_process_loop` awaits handlers serially and inline, throughput is also capped at one webhook round-trip per event across *all* topics: a slow `billing.*` webhook stalls `orders.*` delivery behind it.
- **Evidence**: `webhook.py:43` blocking call; `bus.py:89` `await handler(processed_event)` inside nested serial `for` loops with no `gather` or task spawn. Note `asyncio` is imported in `webhook.py:3` but never used — the intent to go async was never completed.
- **Suggested fix**: Switch to `httpx.AsyncClient`/`aiohttp` with a shared connection pool (both give connection reuse, which `urlopen` also lacks). As a stopgap, wrap the call in `await asyncio.to_thread(...)`. Independently, dispatch handlers concurrently (`asyncio.gather` over matching handlers, or a bounded worker pool) so one slow handler doesn't gate the bus.
- **Effort**: Medium

### [HIGH] `stop()` silently discards queued events and never awaits the worker
- **Category**: Correctness
- **Location**: `eventbus/bus.py:53-56`, `eventbus/bus.py:60-62`
- **Problem**: `stop()` sets `_running = False` and returns immediately. Every event still sitting in the queue (up to 10 000) is dropped without a log line — for an event bus, that is unacknowledged data loss on every graceful shutdown or redeploy. `stop()` also provides no completion barrier: the worker may be mid-`await handler(...)` when `stop()` returns, so callers cannot know when in-flight delivery has finished, and a subsequent `close()` on storage or an HTTP client can race against a handler still using it. The 100 ms `wait_for` poll at `bus.py:62` exists only because there is no real shutdown signal, and it costs 10 needless loop wakeups per second forever.
- **Evidence**:
  ```python
  async def stop(self) -> None:
      self._running = False
      # returns immediately; queue contents lost; worker task not awaited
  ```
- **Suggested fix**: Make `stop()` drain-then-exit: stop accepting publishes, then `await self._queue.join()` (with a timeout and a "dropped N events" log if it expires), enqueue a sentinel to wake the getter, and `await self._task`. Replace the `wait_for(..., timeout=0.1)` poll with a plain `await self._queue.get()` plus the sentinel. Add `__aenter__`/`__aexit__` so shutdown is hard to forget.
- **Effort**: Small

### [HIGH] `RetryMiddleware` performs no retries and leaks one dict entry per event
- **Category**: Correctness
- **Location**: `eventbus/middleware/retry.py:20-26`, `eventbus/middleware/retry.py:28-41`
- **Problem**: `with_retry()` — the only method containing retry logic — is **never called from anywhere in the codebase** (grep-verified: the sole occurrence is its own definition). The bus has no dispatch hook to invoke it. So a user who follows the obvious API and calls `bus.use(RetryMiddleware())` installs a pass-through that does nothing but record `self._retry_counts[event.event_id] = 0`. Concrete harm: operators believe failed webhook deliveries are retried with backoff; in reality `bus.py:92` logs the exception once and the event is discarded. Separately, `_retry_counts` is written for every event and **never read and never deleted** — an unbounded dict that grows for the lifetime of the process (≈100 bytes/event; a bus doing 1 000 events/s leaks ~350 MB/hour).
- **Evidence**:
  ```python
  async def __call__(self, event: Event) -> Event:
      self._retry_counts[event.event_id] = 0   # write-only, never cleaned up
      return event
  ```
  The class docstring even concedes the design gap ("registered as a middleware but actually wraps the dispatch phase") — but nothing implements that wrapping. The trailing `# CLEAN — the retry logic above is correctly implemented` comment at `retry.py:44` is misleading: the logic is fine in isolation and entirely unreachable.
- **Suggested fix**: Give the bus a handler-wrapping extension point distinct from event middleware (e.g. `bus.wrap(retry.with_retry)`) and apply it at `bus.py:89`; or drop the middleware pretence and document `RetryMiddleware.with_retry` as a decorator users apply to their own handlers. Delete `_retry_counts` (it serves no purpose) and add a jitter term plus a max-delay cap to the backoff at `retry.py:39`.
- **Effort**: Medium

### [HIGH] A middleware exception fails *open*: the rest of the chain is skipped but the event is still delivered
- **Category**: Error Handling
- **Location**: `eventbus/bus.py:66-81`; trigger at `eventbus/middleware/logging.py:30`
- **Problem**: When a middleware raises, the handler logs a warning and `break`s out of the chain — then falls through to dispatch with whatever `processed_event` held before the failure. Any middleware *after* the failing one is silently bypassed. Because middleware is the only filtering mechanism (`return None` to drop an event), this makes the whole chain fail-open: if `LoggingMiddleware` is registered before `DeduplicationMiddleware` and hits a payload it can't serialize, dedup never runs and duplicates are delivered. The same bypass would defeat any authorization, schema-validation, or PII-scrubbing middleware a user adds. This is a realistic trigger, not a hypothetical: `logging.py:30` calls `json.dumps(log_data)` with the raw payload, which raises `TypeError` for `bytes`, `datetime`, `Decimal`, or any custom object — and `Event.payload` is typed `dict[str, Any]`, so those are all legal.
- **Evidence**:
  ```python
  except Exception as e:
      logger.warning(f"Middleware error: {e}")
      break            # remaining middleware skipped — event dispatched anyway
  ```
- **Suggested fix**: Decide the policy explicitly and document it. Fail-closed is the safer default for a filter chain: on middleware error, drop the event to the dead-letter path with the failing middleware's name attached, and `continue` rather than dispatch. If fail-open is genuinely wanted, make it per-middleware opt-in. Separately, make `LoggingMiddleware` robust with `json.dumps(..., default=repr)`.
- **Effort**: Small

### [MEDIUM] Webhook redirects silently drop the event body and forward the bearer token to the redirect target
- **Category**: Security
- **Location**: `eventbus/handlers/webhook.py:33-48`, `eventbus/handlers/webhook.py:52-57`
- **Problem**: `urlopen` follows redirects automatically, and CPython's `HTTPRedirectHandler.redirect_request` (verified in `urllib/request.py:633-656`) converts a POST into a **GET** on 301/302/303 and copies all original request headers except `content-length`/`content-type` into the new request. Two consequences for a webhook built by `create_webhook_handler(url, auth_token=...)`: (1) the JSON event body is discarded and the target receives an empty GET — and since the redirect target's 200 comes back to `urlopen`, `response.status >= 400` is false and **nothing is logged**, so events are lost while delivery reports success; (2) the `Authorization: Bearer <token>` header is transmitted to the redirect destination, which may be a different host — so a misconfigured, hijacked, or compromised webhook endpoint exfiltrates the caller's credential with a single 302.
- **Evidence**:
  ```python
  # urllib/request.py:649-655 (stdlib, confirmed)
  CONTENT_HEADERS = ("content-length", "content-type")
  newheaders = {k: v for k, v in req.headers.items() if k.lower() not in CONTENT_HEADERS}
  return Request(newurl, method="HEAD" if m == "HEAD" else "GET", headers=newheaders, ...)
  ```
  ```python
  # webhook.py:55-56 — the header that gets forwarded
  headers["Authorization"] = f"Bearer {auth_token}"
  ```
- **Suggested fix**: Do not follow redirects on webhook delivery — treat 3xx as a delivery failure to be surfaced (with `httpx`, `follow_redirects=False`, which is already the default). If redirects must be followed, drop `Authorization` on cross-origin hops and re-POST the body. Also validate the URL scheme is `https` (or `http` behind an explicit opt-in) in `create_webhook_handler`.
- **Effort**: Small

### [MEDIUM] Dead-letter list is unbounded, has no read/drain API, and conflates two different failures
- **Category**: Performance / Correctness
- **Location**: `eventbus/bus.py:24`, `eventbus/bus.py:84-95`, `eventbus/bus.py:98-105`
- **Problem**: Two problems in the same block. **Growth**: `_dead_letter` is a plain list that is appended to and never trimmed or drained; the only access is `len()` in `status()`. There is no way to inspect, replay, or clear dead letters, so in a long-running process every unroutable event is retained forever with no recovery path — the list is pure leak. A bus with a typo'd subscription pattern accumulates the full event stream in memory. **Semantics**: `dispatched` is set only *after* a handler returns successfully, so "no subscriber matched" and "the matching handler threw" are indistinguishable in the dead-letter stream. Worse, the flag is global across handlers: if two handlers match and one succeeds while the other raises, `dispatched` is `True` and the *failure is dropped entirely* — logged at `bus.py:92` and never dead-lettered, never retried. Partial-delivery failures are therefore invisible to any monitoring built on `status()`.
- **Evidence**:
  ```python
  self._dead_letter: list[Event] = []          # bus.py:24 — no maxlen, no drain
  ...
  await handler(processed_event); dispatched = True   # success flag shared by all handlers
  ...
  if not dispatched: self._dead_letter.append(processed_event)
  ```
- **Suggested fix**: Make it a `collections.deque(maxlen=...)` (configurable, alongside `max_queue_size`) and expose `dead_letters()` / `drain_dead_letters()`. Track per-handler outcomes instead of one boolean, and dead-letter a `(event, pattern, handler, exception)` record for each failure, tagged to distinguish `unroutable` from `handler_error`. Emit a counter for both.
- **Effort**: Small

### [MEDIUM] `MemoryStorage.max_events` does not bound memory, and `get_recent` copies the whole buffer
- **Category**: Performance
- **Location**: `eventbus/storage/memory.py:12-21`, `eventbus/storage/memory.py:28-30`
- **Problem**: The class is documented as storing events "with a configurable max size" and is marked `# CLEAN` — but `max_events` bounds nothing. `_events` is a `deque(maxlen=max_events)` and correctly evicts, however every event is *also* appended to `self._by_topic[event.topic]`, a plain list that is never trimmed. Those lists keep strong references to the very Events the deque evicted, so nothing is ever freed: memory grows linearly and without limit for the process lifetime, exactly the failure `max_events` is supposed to prevent. Separately, `get_recent` materialises the entire deque into a new list before slicing — with the default `max_events=100000`, every call to fetch the last 100 events allocates and copies a 100 000-element list.
- **Evidence**:
  ```python
  self._events: deque[Event] = deque(maxlen=max_events)   # bounded
  self._by_topic: dict[str, list[Event]] = {}             # unbounded, retains evicted events
  ...
  return list(self._events)[-limit:]                      # O(max_events) copy for an O(limit) result
  ```
- **Suggested fix**: Make each `_by_topic` value a `deque(maxlen=...)` too (per-topic cap), or drop the secondary index and filter `_events` on read. For `get_recent`, use `itertools.islice(reversed(self._events), limit)` to avoid the full copy.
- **Effort**: Small

### [MEDIUM] Deduplication is keyed on a random UUID, so it can never deduplicate anything
- **Category**: Correctness
- **Location**: `eventbus/middleware/dedup.py:14-22`, `eventbus/event.py:15`
- **Problem**: `DeduplicationMiddleware` keys `_seen` on `event.event_id`, but `event_id` defaults to `field(default_factory=lambda: str(uuid.uuid4()))` — a fresh random value for every `Event` constructed. Two logically identical events (an at-least-once redelivery from an upstream broker, a client retry, the same order published twice) therefore have different `event_id`s and **both pass the filter**. The middleware only suppresses the case where the identical `Event` object — or an explicitly reused `event_id` — is published twice, which is not the scenario the class documents ("Filters duplicate events within a time window"). Users who add this middleware get a false sense of idempotency plus the O(n) scan cost below, and no dedup.
- **Evidence**: `event.py:15` `event_id: str = field(default_factory=lambda: str(uuid.uuid4()))` vs. `dedup.py:18` `if event.event_id in self._seen:`. Nothing in `create_event` or the bus lets a caller supply a stable id — `create_event(topic, **kwargs)` routes everything into `payload`.
- **Suggested fix**: Key on a deterministic identity: a stable hash of `(topic, canonical-json(payload))`, or an explicit `idempotency_key` field on `Event` that the publisher sets, falling back to the content hash. Document which is used.
- **Effort**: Small

### [MEDIUM] SQLite storage commits (fsyncs) once per event and its index doesn't cover the query
- **Category**: Performance
- **Location**: `eventbus/storage/sqlite.py:45`, `eventbus/storage/sqlite.py:28-31`, `eventbus/storage/sqlite.py:54-58`
- **Problem**: `store()` issues `await self._db.commit()` after every single-row INSERT. Each commit is a durable transaction with an fsync, capping write throughput at roughly 100–1 000 events/s depending on disk — orders of magnitude below what the bus can produce, making storage the bottleneck and inflating disk wear. There is no batch/`store_many` API and no WAL-mode pragma. Separately, `get_by_topic` filters on `topic` and sorts `ORDER BY timestamp DESC`, but the only index is on `topic` alone, so every query does a filesort over all rows for that topic; a hot topic with millions of rows re-sorts the whole partition to return 100 rows.
- **Evidence**:
  ```python
  await self._db.execute("INSERT OR REPLACE INTO events ...", (...))
  await self._db.commit()   # one fsync per event
  ...
  "CREATE INDEX IF NOT EXISTS idx_topic ON events(topic)"          # topic only
  "... WHERE topic = ? ORDER BY timestamp DESC LIMIT ?"            # needs (topic, timestamp)
  ```
- **Suggested fix**: Add `store_many()` that batches N events per transaction, and/or time-based commit batching (commit every k events or every t ms). Set `PRAGMA journal_mode=WAL` and `synchronous=NORMAL` in `initialize()`. Replace `idx_topic` with a composite `events(topic, timestamp DESC)` so the ORDER BY is satisfied by the index.
- **Effort**: Small

### [MEDIUM] Sensitive data reaches logs through two paths: full payloads by default, and secret-bearing webhook URLs
- **Category**: Security
- **Location**: `eventbus/middleware/logging.py:24-30`, `eventbus/handlers/webhook.py:45`
- **Problem**: I traced every logging call site in the package (`logging.py:30`, `bus.py:74`, `bus.py:92`, `webhook.py:45`, `webhook.py:47`, `retry.py:37`, `retry.py:40`). Two leak. (1) `LoggingMiddleware` defaults to `include_payload=True` and serialises the entire `event.payload` at INFO level — for a bus carrying user records, password-reset tokens, or payment details, every event body lands in application logs and any downstream aggregator, with no redaction hook and no allow/deny-list. An opt-out default is the wrong polarity for a library. (2) `webhook.py:45` logs `self.url` verbatim; webhook URLs from Slack, Teams, and Discord embed the shared secret *in the path* (`https://hooks.slack.com/services/T…/B…/<secret>`), so a single non-2xx response writes a live credential to the log. Worth noting the paths I checked that are **clean**: `webhook.py:47` logs `str(e)` from `URLError`/`HTTPError`, which contains the status line but not request headers, so the bearer token does not leak there.
- **Evidence**:
  ```python
  def __init__(self, level: int = logging.INFO, include_payload: bool = True):  # default on
  ...
  log_data["payload"] = event.payload
  logger.log(self.level, json.dumps(log_data))
  ```
  ```python
  logger.error(f"Webhook {self.url} returned {response.status}")   # url may embed a secret
  ```
- **Suggested fix**: Flip `include_payload` to `False` by default and add a `redact: Callable[[dict], dict]` (or field deny-list) for callers who need payload logging. Log a stable identifier instead of the raw URL — the host plus a hash of the path, or a caller-supplied `name` for the handler. Note the `bus.py:92` / `retry.py:40` sites interpolate `{e}`, which can carry payload fragments for exceptions like `KeyError` or pydantic `ValidationError`; consider logging `type(e).__name__` plus a truncated message.
- **Effort**: Small

### [MEDIUM] `unsubscribe()` is not idempotent and silently pollutes the handler registry
- **Category**: Correctness
- **Location**: `eventbus/bus.py:26-36`, `eventbus/bus.py:20`
- **Problem**: The returned closure calls `self._handlers[pattern].remove(handler)`, which raises `ValueError: list.remove(x): x not in list` if invoked twice. An unsubscribe token is normally expected to be safe to call again (from a `finally`, a context-manager exit, or a duplicated teardown path), so this turns idempotent cleanup into an unhandled exception. Because `_handlers` is a `defaultdict(list)`, the subscript *also* creates an empty list for a pattern that has none — so a stray unsubscribe permanently adds a phantom `pattern: 0` entry to `status()["subscriptions"]`, and grows `_handlers` (which the dispatch loop iterates for every event, calling `matches()` against each dead pattern). Finally, if the same handler is subscribed twice to a pattern, `remove` drops only one occurrence, so a single `unsubscribe()` leaves the handler still receiving events — surprising given each `subscribe` returns its own token.
- **Evidence**:
  ```python
  self._handlers: dict[str, list[Handler]] = defaultdict(list)
  ...
  def unsubscribe():
      self._handlers[pattern].remove(handler)   # ValueError on 2nd call; creates empty entry
  ```
- **Suggested fix**: Guard the removal (`try/except ValueError: return`, or check membership), delete the pattern key when its list becomes empty, and use plain `dict` + `setdefault` in `subscribe` so lookups can't create entries. Capture the identity of the specific registration (e.g. wrap in a small token object) so duplicate subscriptions unsubscribe independently.
- **Effort**: Small

### [LOW] Webhook error handling: unreachable status check, unclosed response, and no retryability signal
- **Category**: Error Handling
- **Location**: `eventbus/handlers/webhook.py:43-48`
- **Problem**: `urlopen` raises `HTTPError` for any non-2xx response (`HTTPErrorProcessor.http_error_default` at `urllib/request.py:612-613`), and `HTTPError` subclasses `URLError` (`urllib/error.py:35`). So `if response.status >= 400` at line 44 is **unreachable dead code**, and all HTTP failures land in the `except URLError` branch and are logged as the generic `"Webhook delivery failed"` — collapsing a retryable 503/timeout and a permanent 400/401 into one indistinguishable message with no status code. The response object is also never closed (no `with`), leaving the socket held until GC; under load that accumulates open file descriptors. Both errors and successes re-raise/return without emitting the delivery latency or status, so the handler is effectively unobservable.
- **Evidence**: `response = urllib.request.urlopen(req, timeout=self.timeout)` with no `with`; the `>= 400` branch below it can never execute.
- **Suggested fix**: Catch `HTTPError` before `URLError`, log `e.code`, and re-raise a typed error that distinguishes retryable (5xx, timeout, connection error) from permanent (4xx) so retry logic can act on it. Use `with urlopen(...) as response:`. (Folded into the `httpx` migration in the earlier finding.)
- **Effort**: Small

### [LOW] Storage backends are unreachable from the bus — advertised persistence is unimplemented
- **Category**: Maintainability
- **Location**: `eventbus/storage/sqlite.py`, `eventbus/storage/memory.py`, `eventbus/bus.py`, `pyproject.toml:4`
- **Problem**: `pyproject.toml` describes the package as an "In-process event bus with **persistent storage** and middleware support" and `__init__.py:1` repeats it, but grep confirms `SQLiteStorage` and `MemoryStorage` are referenced nowhere except their own definitions and `storage/__init__.py`'s re-export. `EventBus.__init__` takes no storage parameter and `_process_loop` never calls `store()`. A user following the package description gets a bus that persists nothing and loses all in-flight events on restart; the only way to get persistence is to hand-write a handler that calls `store()`. Neither class is exported from the top-level `eventbus` package either, so they aren't even discoverable from `import eventbus`. With no README and 120 lines of storage code, it is genuinely ambiguous whether this is unfinished work or intentionally BYO-integration.
- **Evidence**: `grep -rn "SQLiteStorage\|MemoryStorage"` → only `storage/__init__.py` and the class definitions.
- **Suggested fix**: Either wire it up — `EventBus(storage=...)` with a `Storage` Protocol, persisting in `_process_loop` before dispatch (which also gives the dead-letter path somewhere durable to go) — or, if BYO is the intent, say so in a README, export the classes from `eventbus/__init__.py`, and show the wiring in an example. Also add the `Storage` Protocol so the two backends are checked against one interface (`MemoryStorage` has `get_recent`/`clear`, `SQLiteStorage` has neither, plus an extra `initialize`/`close` lifecycle — they are not substitutable today).
- **Effort**: Medium

### [LOW] Multi-segment wildcard patterns silently match nothing
- **Category**: Correctness
- **Location**: `eventbus/event.py:19-26`
- **Problem**: `matches()` supports exactly two forms: the literal `"*"` and a trailing `".*"` suffix. Any other wildcard placement — `"orders.*.created"`, `"*.created"`, `"orders.#"` — falls through to `return self.topic == pattern`, an exact string comparison against a pattern containing `*`, which can never be true. A user subscribing to `"orders.*.created"` gets **zero events, no error, and no warning**; the subscription appears healthy in `status()["subscriptions"]` and the events go to the dead-letter list. The docstring's claim of "glob pattern" matching invites exactly this mistake.
- **Evidence**:
  ```python
  if pattern == "*": return True
  if pattern.endswith(".*"): ...
  return self.topic == pattern      # "orders.*.created" reaches here → always False
  ```
- **Suggested fix**: Validate the pattern in `subscribe()` and raise `ValueError` on unsupported wildcard placement — a loud failure at registration beats silent zero-delivery. Fix the docstring to say "prefix match" rather than "glob", or implement real segment-wise matching via `fnmatch`/a compiled regex.
- **Effort**: Small

### [LOW] In-source `BUG` annotations are self-contradictory and misidentify a non-issue
- **Category**: Maintainability
- **Location**: `eventbus/middleware/dedup.py:35-41`, `eventbus/storage/sqlite.py:51-53`, `eventbus/storage/memory.py:8`, `eventbus/middleware/retry.py:44`
- **Problem**: The codebase carries `BUG [...]` / `CLEAN` comments that actively misinform. `dedup.py:35-41` opens with `BUG [CORRECTNESS]: ... drops the NEWEST entries`, then reverses itself mid-comment ("Actually wait — let me re-examine", "The comment above about dropping newest is wrong"). The code is in fact correct: entries are never re-inserted (duplicates return early at line 19), so dict insertion order equals timestamp order and `list(keys)[:excess]` evicts oldest-first. `sqlite.py:51-53` flags `BUG [SECURITY]: SQL injection` on a properly parameterized query and then annotates itself as "a false positive trap for the auditor." Meanwhile `memory.py:8` marks a class `CLEAN — ... should NOT be flagged` that has an unbounded-growth bug, and `retry.py:44` certifies as `CLEAN` a method that is never called. The net effect is that anyone triaging this file — human or tooling — is pointed away from the real defects and toward two non-issues.
- **Evidence**: `dedup.py:37` `# but the logic below drops from the end). Actually wait — let me re-examine.`
- **Suggested fix**: Delete the contradictory and trap comments. Where a subtle invariant genuinely needs explaining (dedup's reliance on insertion order tracking timestamp order), state the invariant as a normal comment and add a test that fails if it's violated. Move real known defects to the issue tracker rather than leaving verdicts in source.
- **Effort**: Small

### [LOW] Dedup cleanup is O(n) on every event
- **Category**: Performance
- **Location**: `eventbus/middleware/dedup.py:16`, `eventbus/middleware/dedup.py:24-33`
- **Problem**: `_cleanup_expired()` runs on *every* event and builds a full list comprehension over all of `_seen` to find expired keys. With the default `max_seen=10000`, that's a 10 000-element scan plus a temporary list allocation per event — at 1 000 events/s, ten million dict lookups per second spent on housekeeping. The `max_seen` trim then does a second full `list(self._seen.keys())` materialisation.
- **Evidence**: `expired = [k for k, ts in self._seen.items() if now - ts > self.window_seconds]` executed per event.
- **Suggested fix**: Keep a parallel `deque` of `(event_id, timestamp)` in insertion order and pop from the left while the head is expired — amortised O(1) per event, and the same deque handles the `max_seen` cap. Alternatively throttle cleanup to run at most once per second.
- **Effort**: Small

### [LOW] Test suite relies on wall-clock sleeps, covers one module, and has no CI
- **Category**: Maintainability
- **Location**: `tests/test_bus.py:18`, `tests/test_bus.py:38`, `tests/test_bus.py:49`, `pyproject.toml`
- **Problem**: All three tests use `await asyncio.sleep(0.2)` as their synchronisation barrier. On a loaded CI runner that window is not reliably enough for the 100 ms-poll worker to drain the queue, so these are flaky by construction — and they fail *silently correct-looking* (an assertion on `len(received)`) rather than pointing at the timing. Coverage is `bus.py` only: nothing exercises either storage backend, any of the three middleware, the webhook handler, `unsubscribe`, queue backpressure, or shutdown — which is why the several no-op/never-called code paths above went unnoticed. Because `stop()` never awaits the worker task (see earlier finding), each test also leaks a pending task into the closed event loop. There is no CI config anywhere in the repo and no `[tool.pytest.ini_options]` in `pyproject.toml`, so `asyncio_mode` is unset and the `@pytest.mark.asyncio` markers depend on ambient pytest-asyncio configuration to run at all.
- **Evidence**: `await asyncio.sleep(0.2)` in all three tests; `find` for `*.yml`/`*.yaml` returns nothing; `pyproject.toml` has only `[project]` and `[project.optional-dependencies]`.
- **Suggested fix**: Replace the sleeps with a deterministic barrier — `await bus.stop()` once it drains, or an `asyncio.Event` the test handler sets. Add `[tool.pytest.ini_options] asyncio_mode = "auto"` and a minimal CI workflow running pytest on 3.11–3.13. Prioritise tests for the middleware chain (especially the error path) and both storage backends.
- **Effort**: Medium

## Issues Not Found

- **SQL injection / command injection / path traversal**: none. `sqlite.py:39-44` and `sqlite.py:54-58` use parameterized queries correctly, including the `LIMIT ?` placeholder; there is no string interpolation into SQL anywhere. No `subprocess`, `os.system`, `eval`, or `pickle` usage. The `BUG [SECURITY]: SQL injection` comment at `sqlite.py:51` is a false positive by its own admission.
- **Hardcoded secrets**: none. `create_webhook_handler` takes the token as a parameter; no keys, tokens, or connection strings are embedded in source. (The token *handling* has issues — see the redirect and logging findings — but nothing is committed.)
- **Weak cryptography / predictable randomness**: no crypto in the codebase. `uuid.uuid4()` is the only randomness and is appropriate for event ids (the problem there is semantic, not entropy).
- **CORS / authentication / authorization**: not applicable — this is an in-process library with no network listener.
- **Dependency vulnerabilities**: not assessable. Only two runtime dependencies (`aiosqlite>=0.19.0`, `pydantic>=2.0.0`) with open-ended lower bounds, and no lock file to pin against. Worth noting `pydantic` is declared but **never imported anywhere** in the package — `Event` is a stdlib `@dataclass`, so this is an unnecessary heavyweight dependency that should be dropped (or `Event` migrated to `BaseModel` if validation was the intent).
- **N+1 query patterns**: none. `get_by_topic` and `count` each issue exactly one query; there are no loops around DB calls.
- **Off-by-one / boundary errors**: I checked the slice and range boundaries — `events[-limit:]`, `list(self._events)[-limit:]`, `pattern[:-2]`, `list(keys)[:excess]`, and `range(self.max_retries + 1)` are all correct. The only nit is cosmetic: `retry.py:40` logs `Retry {attempt+1}/{max_retries}` while the loop actually makes `max_retries + 1` attempts, so the final message reads "Retry 3/3" before a 4th attempt runs.
- **Circular dependencies**: none. Import direction is clean and strictly inward (`middleware`/`storage`/`handlers` → `event`; `bus` → `event`), with no back-references from the leaf modules to `bus`.
- **Type coercion bugs**: not applicable (Python); type hints are consistent, though `headers: dict[str, str] = None` (`webhook.py:18`) and `auth_token: str = None` (`webhook.py:52`) should be `| None` for correct static checking.