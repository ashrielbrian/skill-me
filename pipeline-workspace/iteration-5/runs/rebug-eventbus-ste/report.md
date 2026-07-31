I read the report from `./audit-report.md` in the working directory. I read all 11 source files and the one test file. I also read the CPython standard library source on this machine to check the `urllib` and `json` claims. Python execution needed an approval that this non-interactive session cannot give, so I verified the runtime claims from the standard library source and from language semantics, not from a test run.

# Issue Validation Report

## Summary

I reviewed all 18 findings and all 7 entries in the "Issues Not Found" section.

Verdict counts:
- Confirmed: 14
- Confirmed, reseveritied: 2 (one down, one up)
- Partially valid: 2
- Disputed: 0
- Needs more context: 0

The report is accurate. The reporter read the code correctly and did not invent a defect. Every one of the 7 "Issues Not Found" entries is also correct, and the reporter caught two false traps that the source comments plant: the SQL injection comment at `sqlite.py:51` and the eviction order comment at `dedup.py:35`. The reporter also correctly flagged `MemoryStorage` even though `memory.py:8` says "CLEAN — this entire class is well-implemented and should NOT be flagged". The code in that class is genuinely defective.

The weak part of the report is the fixes, not the findings. 8 of the 18 proposed fixes have a defect. Three would add a new bug:
- The fix for `stop()` deadlocks the shutdown, because `_process_loop` never calls `task_done()`.
- The fix for `Event` breaks `json.dumps` at three call sites.
- The fix for `DeduplicationMiddleware` silently drops distinct events.

I also found 5 issues that the report does not list.

## Detailed Findings

### Issue: An exception in the loop that dispatches events stops the bus without a signal
- **Original severity**: Critical
- **Verdict**: Confirmed
- **Investigation**: The statement `for pattern, handlers in self._handlers.items():` at `eventbus/bus.py:85` is not in a `try` block. The `try` block at lines 88-92 covers the handler call only. `self._handlers` is a `defaultdict(list)` (line 20), and `subscribe()` at line 28 writes `self._handlers[pattern].append(handler)`. A new pattern adds a key and changes the size of the dictionary. Python then raises `RuntimeError: dictionary changed size during iteration` at the next step of the loop, and the exception leaves `_process_loop`.

  The trigger is wider than the report states. Line 89 awaits the handler, so control returns to the event loop there. Any other task in the process can then call `subscribe()` with a new pattern. A dynamic subscription from a concurrent task is enough.

  I confirmed all three downstream effects. Nothing sets `_running = False`, so `status()` at line 101 still reports `"running": True`. `publish()` at line 44 tests `self._running` only, so it still accepts events. The queue then fills to `max_queue_size`, and `await self._queue.put(event)` at line 46 blocks every publisher.

  One correction to the report: asyncio does log the exception. The default exception handler writes "Task exception was never retrieved" when the garbage collector frees the Task. That message does not name the bus, and `status()` still reports a healthy bus, so the operational effect is as the report describes.

  The discarded task has a second effect that the report does not name. The asyncio documentation tells the caller to keep a reference to the result of `create_task`. Without a reference, the garbage collector can free a task that is still pending.
- **Fix assessment**: All four steps are correct. `except Exception` is the right choice, because `CancelledError` derives from `BaseException` in Python 3.8 and later. Normal cancellation still works.
- **Alternative fix**: Add two steps. Set `self._running = False` in a `finally` block, so `status()` reports the true state. Change line 87 to `for handler in list(handlers):` at the same time, because Issue 14 needs the same edit.

### Issue: The bus delivers an event after a middleware raises an exception
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: I traced `eventbus/bus.py:67-81`. The assignment `processed_event = await mw(processed_event)` at line 70 does not complete when the middleware raises. `processed_event` keeps the previous value. The `except` block logs a warning and breaks. The test at line 80 then sees a value that is not `None`, and the bus delivers the event. The chain fails open, and the bus skips every middleware after the one that failed.

  The report is more accurate here than the source comment. The comment at lines 75-77 says the bus continues with "the ORIGINAL event". That is wrong. The bus continues with the output of the last middleware that succeeded.

  The example in the report is correct. `LoggingMiddleware` calls `json.dumps` on the payload at `logging.py:30`. A `bytes` value raises `TypeError`. The bus then skips `DeduplicationMiddleware` and delivers the duplicate.
- **Fix assessment**: The fix works but it trades one silent failure for another. `processed_event = None` sends the flow to `continue` at line 81. That path skips the dead-letter list at line 95, so the event disappears with one warning only. A `TypeError` in a log middleware would then destroy business events.
- **Alternative fix**: Do three things. Raise the log level from `warning` to `error`, because the bus loses a guarantee at this point. Add the event to the dead-letter list, so the drop leaves a record. Add a `fail_open` attribute that each middleware sets, and read it in the `except` block. Set the default to fail closed.

### Issue: `stop()` discards every queued event
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: `stop()` at `eventbus/bus.py:53-55` sets one flag. The loop condition at line 60 is `while self._running`. Every event still in the queue is lost.

  The second claim is also correct. `stop()` returns at once, but the loop tests `_running` at the top only. If the loop is inside `wait_for` at line 62 when the flag changes, it still receives that event and runs the full middleware chain and the full dispatch. So the bus can deliver an event after `stop()` returns.

  The third claim is correct. `start()` at lines 48-51 has no guard. Two calls create two `_process_loop` tasks. Both consume the same queue, so each event goes to one task only, and the two tasks interleave.
- **Fix assessment**: The first step is wrong and would hang the shutdown. `asyncio.Queue.join()` waits until the count of unfinished tasks reaches zero. Every `put()` raises that count, and only `task_done()` lowers it. `_process_loop` never calls `task_done()`, so `join()` blocks forever. The order in the fix also fails: `stop()` sets `_running = False` first, so the loop stops to consume, and `join()` can never complete.
- **Alternative fix**: Use four steps in this order.
  1. Add `self._queue.task_done()` at the end of each pass of `_process_loop`. Put it in a `finally` block.
  2. Keep the task object in `self._task` in `start()`, and add the guard that the report proposes.
  3. In `stop()`, set a new flag `self._draining = True` and change the loop condition to `while self._running or not self._queue.empty()`.
  4. Then `await asyncio.wait_for(self._task, timeout)`, and cancel the task if the timeout expires.

  The Medium effort estimate is right, but the work is larger than the report implies.

### Issue: `RetryMiddleware` gives no retry and leaks memory
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: I searched the whole package for `with_retry`. The only match is the definition at `retry.py:28`. No caller exists. `_process_loop` calls `await handler(processed_event)` directly at `bus.py:89`. So `bus.use(RetryMiddleware())` gives no retry, and the bus prints no warning.

  The leak is also real. `__call__` at `retry.py:25` writes `self._retry_counts[event.event_id] = 0` for every event. I searched for `_retry_counts`. The only matches are the definition at line 20 and that one write. No code reads the dictionary and no code removes an entry, so it grows for the life of the process.

  The class docstring at lines 11-15 states the intended design: "It's registered as a middleware but actually wraps the dispatch phase." The bus never completes that design.

  A failed handler has one more effect that the report does not name. `dispatched = True` at line 90 runs only after the handler returns. So a matched handler that raises sends the event to the dead-letter list, as if no handler matched.
- **Fix assessment**: The second and third steps are right. The first step is the wrong shape. The bus would need `isinstance(mw, RetryMiddleware)` to find the retry object in a list of plain callables. That adds a special case for one class to the core loop.

  The first step also has a worse problem. The bus has one `_process_loop`. A retry inside the dispatch loop stops every other event for the length of the backoff. With the default values the delays are 1 s, 2 s, and 4 s, so one bad handler halts the bus for 7 s. That makes Issue 6 worse.
- **Alternative fix**: Make `RetryMiddleware` a handler wrapper instead of a middleware. Give it a `wrap(handler)` method that returns a coroutine function. The user then writes `bus.subscribe(pattern, retry.wrap(handler))`. Delete `__call__`, or make it raise a clear `TypeError` that names `wrap`. This keeps the bus free of special cases. Add a bound on the total backoff, or dispatch each handler as a separate task, so a retry does not stop the bus.

### Issue: `LoggingMiddleware` writes the full event payload to the log by default
- **Original severity**: High
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: Medium
- **Investigation**: The facts are correct. `include_payload` defaults to `True` at `logging.py:12`, and line 28 copies the complete payload into the log record.

  I lowered the severity for three reasons. The user must register this middleware; the bus does not add it. The data goes to the log sink of the application, not across a trust boundary. The class name states its purpose, so the behavior is visible to the person who adds it. This is a poor secure default, which is a Medium concern, not a High one.
- **Fix assessment**: All three steps are correct and cheap. A `redact_keys` parameter is the right addition.
- **Alternative fix**: Add one more change. Move `json.dumps` behind a level check, because the current code builds the string even when the log level is off. See the first new issue below.

### Issue: `WebhookHandler` blocks the event loop with synchronous HTTP
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: `__call__` at `webhook.py:24` is a coroutine function, but line 43 calls `urllib.request.urlopen`. That call is synchronous and it does not yield to the event loop. The default `timeout` is 30 at line 19.

  The effect is as bad as the report states, and the report understates one part. The block stops the whole event loop, so it stops every other task in the process, not the bus alone. A slow endpoint holds the loop for up to 30 seconds. The bus has one `_process_loop`, so no parallel path exists.
- **Fix assessment**: The order of the two options is wrong, and the dependency advice is wrong. Do not add both `aiohttp` and `httpx` to a library that has no HTTP dependency today. Pick one, or add neither.

  `asyncio.to_thread` is the better primary fix, not a short-term step. It needs no new dependency and it frees the event loop. Note the limit: `_process_loop` still awaits the handler, so the bus itself still waits 30 seconds. Only other tasks in the process get relief.
- **Alternative fix**: Use `await asyncio.to_thread(...)` for the HTTP call. `webhook.py:3` already imports `asyncio` and nothing uses it. To remove the delay for the bus as well, dispatch each handler as a task in `_process_loop` instead of an inline `await`. Treat an optional `httpx` extra as a later step, not as a required dependency.

### Issue: `MemoryStorage` has no limit on the per-topic index
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: The `maxlen` at `memory.py:13` bounds `_events`. Lines 19-21 append the same event to a plain list in `_by_topic`, and no code trims that list. `clear()` is the only method that empties it.

  The effect is worse than a partial leak. An event that the deque evicts stays in `_by_topic`, so `_by_topic` holds a strong reference and the `maxlen` frees no memory at all. The `max_events` parameter gives no memory bound. A topic name that holds a variable part, such as an order identifier, also adds one key per event.

  The docstring at line 10 promises "a configurable max size", so the class breaks its own stated contract. The "CLEAN" comment at line 8 is wrong.
- **Fix assessment**: The first step is incomplete. A `deque` with a `maxlen` for each topic bounds each list, but total memory then becomes the count of topics times the `maxlen`. That is still far above `max_events`, and a new topic per event still grows the dictionary. The second step and the third step are both sound; the third is the strongest.
- **Alternative fix**: Prefer the third option. Delete `_by_topic` and build the result of `get_by_topic` from `_events` at query time. With a limit of 100000 events, one filter pass is fast, and the class then has one source of truth and one true bound. Keep the index only if the query rate needs it. In that case, capture the event that the deque will evict before each append, and remove it from its topic list in the same step.

### Issue: The dead-letter list has no size limit
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: `self._dead_letter` is a plain list at `bus.py:24`. Line 95 appends to it, and no code removes an entry. `status()` at line 104 reads the length only, so the class exposes no way to read or clear the events.

  Both triggers in the report are real. A wrong pattern sends every event here. A handler that always raises also sends every event here, because `dispatched` stays `False` (see Issue 4). A third trigger is more common than either: a bus with no subscriber yet retains every event that arrives before the first `subscribe()` call.
- **Fix assessment**: All three steps are correct. A `deque` with a `maxlen` drops the oldest dead letters, which are usually the most useful for diagnosis, but the counter records the loss. That trade is acceptable.

### Issue: An HTTP redirect drops the webhook body and sends the auth token to a new host
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: I read the `urllib` source on this machine to check each claim. The claims are correct.

  `HTTPRedirectHandler.redirect_request` contains this code:
  ```python
  CONTENT_HEADERS = ("content-length", "content-type")
  newheaders = {k: v for k, v in req.headers.items()
                if k.lower() not in CONTENT_HEADERS}
  return Request(newurl, headers=newheaders, ...)
  ```
  The new `Request` has no `data`, so its method is `GET`. The filter removes two headers only. `Authorization` passes to the new host.

  I searched the same file for `Authorization`. `urllib` protects `Proxy-Authorization` for a tunnel, at the comment "Proxy-Authorization should not be sent to origin server." No equivalent protection exists for `Authorization` on a redirect. The `requests` library strips that header across hosts; `urllib` does not.

  The status codes matter. For 301, 302, and 303 on a POST, `urllib` follows the redirect and drops the body. For 307 and 308 on a POST, `redirect_request` raises `HTTPError`, so those codes fail loudly. Only the first three codes cause the silent failure.

  `create_webhook_handler` at `webhook.py:52-57` passes the token through the `headers` argument of `WebhookHandler`. The handler passes it to the `Request` constructor at line 36, which makes it a redirectable header.

  Note on versions: I read the 3.9 standard library, because this machine has no 3.11 source and the project needs 3.11 or later. This part of `urllib` is unchanged in 3.11 through 3.13, except that 3.11 adds 308 to the codes for GET and HEAD.
- **Fix assessment**: All three steps are correct. The report does not name the `urllib` mechanism that solves the third step directly.
- **Alternative fix**: Use `req.add_unredirected_header("Authorization", ...)` instead of the `headers` argument. `urllib` never sends an unredirected header on a redirect. To stop the redirect itself, install an opener with a subclass of `HTTPRedirectHandler` whose `redirect_request` returns `None`. If you replace `urllib` for Issue 6, set `follow_redirects=False` on the new client instead.

### Issue: `DeduplicationMiddleware` cannot detect a duplicate event
- **Original severity**: Medium
- **Verdict**: Partially valid
- **Revised severity**: Low
- **Investigation**: The facts are right, but the conclusion overstates them. `event_id` at `event.py:15` uses `default_factory=lambda: str(uuid.uuid4())`, so each `Event` object gets a new random identifier. `dedup.py:18` keys on `event.event_id`. Two events with an equal topic and an equal payload have different identifiers, and both pass.

  The middleware is not broken, though. A dedup filter on a producer-assigned identifier is the standard idempotency key pattern in a message system. It catches the case it is built for: the same event that arrives twice. `Event` accepts `event_id` as a normal field, so a producer can supply a stable value. The title "cannot detect a duplicate event" is therefore too strong. The middleware detects a duplicate whenever the caller supplies the identifier.

  The real defect is narrow. The docstring at `dedup.py:7` does not state the requirement, and the default factory hides it. This library also has no republish path of its own, so nothing inside the package can trigger the filter. That makes it a documentation defect plus a missing option, which is Low.
- **Fix assessment**: The second step is right. The first step would add a new bug. A default `key_fn` that hashes the topic and the payload drops distinct events that share a payload. Two heartbeat events one second apart, or two identical `item.clicked` events from one user, would both collapse to one. That is silent data loss in exchange for a feature that few callers want by default.
- **Alternative fix**: Keep `event.event_id` as the default key. Add an optional `key_fn` parameter for the caller who wants content-based dedup. Correct the docstring to state that the caller must supply a stable `event_id`. Do not change the default.

### Issue: The two storage backends return `get_by_topic` results in opposite order
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: The two orders differ, as the report states. `sqlite.py:56` uses `ORDER BY timestamp DESC`, so the newest event comes first. `memory.py:26` returns `events[-limit:]` from a list in insertion order, so the oldest of the returned events comes first. No docstring and no test records an intended order.

  One point weakens the premise and one point strengthens the finding. The two classes share no base class and no `Protocol`, so nothing enforces a common shape. But the gap between them is larger than the order alone: `MemoryStorage` has `get_recent` and `clear`, and `SQLiteStorage` has neither; `SQLiteStorage` has `initialize` and `close`, and `MemoryStorage` has neither. The two classes are not substitutable at all. I list this as a separate new issue below.
- **Fix assessment**: All three steps are correct. Newest first is the better choice, because it matches the word "recent" in both docstrings and it matches the SQL that already exists.

### Issue: `SQLiteStorage.store` commits once per event and can raise `TypeError`
- **Original severity**: Medium
- **Verdict**: Partially valid
- **Investigation**: Both defects are real. `sqlite.py:45` calls `await self._db.commit()` after each single insert. Each commit forces a disk sync. The stated rate of "a few hundred events per second" is an estimate that nobody measured, but the order of magnitude is reasonable for the default rollback journal mode.

  `json.dumps(event.payload)` at line 42 raises `TypeError` for a `bytes` value, a `datetime` value, or a custom object. No `try` block covers it, so the exception leaves `store()`.

  One implication in the report is wrong. It says "Inside the bus, the dispatch code catches the exception and only logs it". The bus never calls storage. I searched `bus.py` for `storage` and `store`; there is no match, and Issue 18 in the same report states this. The `TypeError` reaches whichever code calls `store()`. If the caller is a handler that the user registered, then the bus catches it at `bus.py:91`. That is a possible path, not the path.

  A third defect exists at line 40 that the report does not name. `INSERT OR REPLACE` overwrites a row that has the same `event_id`. A duplicate identifier destroys the earlier row without a signal.
- **Fix assessment**: The `PRAGMA journal_mode=WAL` step is the strongest one, and the `ValueError` step is right. The `store_many` step misses the main use case. The intended pattern stores one event per published event, so a batch method does not help that caller. The single-event path needs the pragma change.
- **Alternative fix**: Set `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` in `initialize()`. Together they remove most of the per-commit cost and keep crash safety for the database file. Wrap `json.dumps` in a `try` block and raise a `ValueError` that names the topic and the identifier. Do not pass `default=str` to `json.dumps`, because that silently changes the type on a round trip. Add `store_many` only if a real batch caller appears. Close the cursors in `get_by_topic` and `count`.

### Issue: The cleanup of the deduplication cache scans every entry for each event
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: `__call__` calls `_cleanup_expired()` at `dedup.py:16` on every event. That method builds a list comprehension over all of `_seen` at lines 30-31. The cost is O(n) per event and O(n²) for a burst of n events. The bound is `max_seen`, which defaults to 10000. The cleanup runs before the insert and only one entry arrives per call, so the length never exceeds `max_seen + 1`.

  The absolute cost is under a millisecond per event, so this matters only at a high event rate. At 1000 events per second it consumes a large part of one core. Medium is the right severity for a cost that the bus pays on every event and that a small change removes.
- **Fix assessment**: The approach is correct, and the code needs less than the fix states. A plain `dict` already keeps insertion order, and a duplicate returns at line 19 before any re-insert, so insertion order already equals first-seen order. You do not need `OrderedDict`.
- **Alternative fix**: Keep the plain `dict`. Replace the comprehension with a loop that reads the front and stops at the first entry inside the window:
  ```python
  while self._seen:
      k = next(iter(self._seen))
      if now - self._seen[k] <= self.window_seconds:
          break
      del self._seen[k]
  ```
  This gives amortized O(1) cleanup with no new import. Switch to `time.monotonic()` at the same time; see the new issue below.

### Issue: The unsubscribe function raises an error and corrupts dispatch
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: Both parts hold. The closure at `bus.py:34` calls `self._handlers[pattern].remove(handler)`. A second call raises `ValueError: list.remove(x): x not in list`, because the handler is no longer in the list.

  The dispatch problem is real. Line 87 iterates the live list object that `remove` changes. When a handler unsubscribes itself, the index of the iterator has already advanced, so the loop skips the next handler for that pattern. That handler never receives the event.

  The dictionary stays safe in this case. `remove` deletes a list element and leaves the key, so the size of `_handlers` does not change and no `RuntimeError` follows.

  The source comment at lines 31-33 names a third case that the report does not repeat. The same handler subscribed twice to one pattern needs two `unsubscribe()` calls, and each call removes one entry.
- **Fix assessment**: Both steps are correct and both are needed. The copy at line 87 also fixes part of Issue 1.
- **Alternative fix**: Use a guarded removal rather than `except ValueError`, so the code states the intent:
  ```python
  def unsubscribe():
      handlers = self._handlers.get(pattern)
      if handlers and handler in handlers:
          handlers.remove(handler)
  ```
  Note the behavior that the copy creates: a handler that unsubscribes during dispatch still receives the current event. State that in the docstring of `subscribe`.

### Issue: Tests cover only `bus.py` and depend on fixed sleep calls
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: `tests/test_bus.py` holds 3 test functions and all 3 test `EventBus`. `tests/` holds only that file and an empty `__init__.py`. No test covers `SQLiteStorage`, `MemoryStorage`, `LoggingMiddleware`, `RetryMiddleware`, `DeduplicationMiddleware`, or `WebhookHandler`.

  Each test calls `await asyncio.sleep(0.2)` at lines 18, 38, and 49. The `_process_loop` timeout is 0.1 s, so a loaded machine can miss the window and fail the test.

  I listed every file, including hidden files. The repository holds no README, no `.github` directory, and no other CI configuration file. `pyproject.toml` also has no `[tool.pytest.ini_options]` section, but the tests carry `@pytest.mark.asyncio`, which the strict default mode of `pytest-asyncio` needs. I could not run `pytest` here, so I did not confirm that the 3 tests pass. By inspection all 3 should pass.

  Each test has one more flaw that the report does not name. No test awaits the `_process_loop` task after `stop()`. The task is still pending when `pytest-asyncio` closes the loop, so each test can emit "Task was destroyed but it is pending!".
- **Fix assessment**: All three steps are correct. The `asyncio.Event` step removes the timing risk and it is the right change.
- **Alternative fix**: Add one step. Fix Issue 3 first. A `stop()` that drains the queue and awaits the task makes the tests deterministic by construction, and then the `asyncio.Event` guard covers the handler side only.

### Issue: `SQLiteStorage` has no async context manager and leaks the connection
- **Original severity**: Low
- **Verdict**: Confirmed
- **Investigation**: The class has no `__aenter__` and no `__aexit__`, so `async with SQLiteStorage(...)` raises `TypeError`. `close()` at lines 73-77 is the only cleanup path, and the caller must remember it. `initialize()` at line 18 assigns `self._db` without a check, so a second call replaces the first connection and leaks it. Low is the right severity, because the caller must make a mistake first.
- **Fix assessment**: Both steps are correct and the effort estimate is right.

### Issue: `Event` is frozen, but the payload dictionary stays mutable
- **Original severity**: Low
- **Verdict**: Confirmed
- **Investigation**: Both claims hold. `frozen=True` at `event.py:10` blocks attribute assignment only. The `payload` dictionary at line 14 stays mutable, and every handler receives the same object, so one handler can change what the next handler sees.

  The hash claim is also right. `frozen=True` with the default `eq=True` makes the dataclass generate `__hash__` from the tuple of all fields. That tuple holds a `dict`, so `hash(event)` raises `TypeError: unhashable type: 'dict'`. An `Event` cannot enter a `set` or serve as a dictionary key. `DeduplicationMiddleware` is safe, because it keys on the `event_id` string, not on the event.
- **Fix assessment**: The `MappingProxyType` step would break the package. Three call sites pass the payload to `json.dumps`: `logging.py:30`, `sqlite.py:42`, and `webhook.py:26`. The JSON encoder dispatches on `isinstance(o, dict)` (`json/encoder.py:430` and the nested checks at lines 321 and 401). `MappingProxyType` is not a `dict` subclass, so `json.dumps` falls through to `default()` and raises `TypeError: Object of type mappingproxy is not JSON serializable`. The webhook case is the worst, because the payload sits inside another dictionary and the nested check fails in the same way. The two other steps in the fix are correct.
- **Alternative fix**: Do not use `MappingProxyType`. Take three smaller steps.
  1. Correct the docstring at line 12 to say that the attributes are immutable and the payload is not.
  2. State the convention that a handler must not change the payload. Add a shallow `dict(payload)` copy in `__post_init__` with `object.__setattr__` if you want to break the alias to the caller. Note the limit: a shallow copy does not protect a nested value, and it costs one copy per event.
  3. Define `__hash__` from `event_id` alone, and set `eq=False` or write `__eq__` to match.

### Issue: The bus never uses a storage backend, but the description promises persistence
- **Original severity**: Low
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: Medium
- **Investigation**: The facts are correct. `pyproject.toml:4` says "In-process event bus with persistent storage and middleware support", and `eventbus/__init__.py:1` repeats the claim. I searched `bus.py` for `storage` and `store`. There is no match. `EventBus.__init__` at line 19 takes `max_queue_size` only, and every event lives in the `asyncio.Queue` alone. A restart loses all of them. No README explains the manual wiring.

  I raised the severity for two reasons. The first claim in the package description names a feature that does not exist, and the gap causes data loss for a user who trusts it. `eventbus/__init__.py` also sets `__all__ = ["EventBus", "Event"]`, so the storage classes are not even visible from the top-level package. A user must find `eventbus.storage.sqlite` alone. A false persistence promise in a released version 1.3.0 is more than a Low documentation defect.
- **Fix assessment**: The steps are correct and the Medium effort estimate is right. Watch one detail: a `store()` call inside `publish()` makes `publish()` wait for a disk commit, which meets Issue 12. Fix the pragma settings in the same change, or store from `_process_loop` and accept that the bus can lose an event between `publish` and `store`.
- **Alternative fix**: Do the smaller step first. Correct the description in `pyproject.toml` and in `eventbus/__init__.py` to state what the code does today. Then add the `storage` parameter as a separate change. A wrong promise is worse than a missing feature.

## New Issues Discovered

### [Medium] `LoggingMiddleware` builds the log string before the level check, so a bad payload breaks the middleware chain

- **Category**: Error Handling
- **Location**: `eventbus/middleware/logging.py:18-31`, `eventbus/bus.py:73-78`
- **Problem**: Line 30 reads `logger.log(self.level, json.dumps(log_data))`. Python evaluates `json.dumps` before the call, so the middleware builds the string even when the log level is off. Two results follow. The middleware pays the full serialization cost for a record that nobody writes. Worse, a payload that holds `bytes`, a `datetime`, or a custom object raises `TypeError` at that point, even when the operator turns the level down. That exception then meets the fail-open path in Issue 2, so the bus skips every later middleware and still delivers the event. A `DeduplicationMiddleware` or a redaction step after the log middleware becomes a no-op, and the only signal is one warning line.
- **Evidence**:
  ```python
  logger.log(self.level, json.dumps(log_data))
  ```
- **Suggested fix**: Guard the call with `if not logger.isEnabledFor(self.level): return event`. Wrap `json.dumps` in a `try` block and pass `default=repr` for the payload, so a log call never breaks the chain.
- **Effort**: Small (< 1 hour)

### [Medium] The dead-letter list cannot separate "no handler matched" from "every handler failed"

- **Category**: Correctness
- **Location**: `eventbus/bus.py:84-95`
- **Problem**: `dispatched` becomes `True` only after a handler returns without an exception. Three different outcomes therefore share one result. An event with no matching pattern goes to the dead-letter list. An event whose only handler raises also goes to the dead-letter list, so the list cannot show which case occurred. The third outcome is the worst: when one of two handlers succeeds and the other raises, `dispatched` is `True`, so the event does not reach the dead-letter list and the failed delivery leaves only a log line. An operator who watches `status()["dead_letters"]` sees no sign of the partial delivery.
- **Evidence**:
  ```python
  for handler in handlers:
      try:
          await handler(processed_event)
          dispatched = True
      except Exception as e:
          logger.error(f"Handler error for {pattern}: {e}")
  ```
- **Suggested fix**: Count matched handlers and failed handlers separately. Add an entry to the dead-letter list for each failed handler, with the pattern and the exception. Report both counts in `status()`.
- **Effort**: Small (< 1 hour)

### [Low] `WebhookHandler` has three defects in its error handling

- **Category**: Error Handling
- **Location**: `eventbus/handlers/webhook.py:42-48`
- **Problem**: The `try` block has three problems.
  1. The branch `if response.status >= 400` is unreachable. `urllib.error.HTTPError` derives from `URLError` (`urllib/error.py:35`), and `urlopen` raises it for any 4xx or 5xx status. Control never reaches the status test.
  2. `except urllib.error.URLError` does not catch a read timeout. `urllib` wraps an `OSError` from `h.request()` in a `URLError`, but `h.getresponse()` at `urllib/request.py:1350` sits outside that wrapper. A timeout there raises `TimeoutError`, which is an `OSError` but not a `URLError`. The exception escapes to `bus.py:91` and appears as a handler error, not as a webhook failure.
  3. The code never closes `response`. The socket and the file object stay open until the garbage collector frees them, and Python can emit a `ResourceWarning`.
- **Evidence**:
  ```python
  response = urllib.request.urlopen(req, timeout=self.timeout)
  if response.status >= 400:
      logger.error(f"Webhook {self.url} returned {response.status}")
  except urllib.error.URLError as e:
  ```
- **Suggested fix**: Use `with urllib.request.urlopen(...) as response:`. Catch `OSError` instead of `URLError`, because `OSError` covers both `URLError` and `TimeoutError`. Add a separate `except urllib.error.HTTPError` clause before it, and log the status code from `e.code`. These changes become simpler if you replace `urllib` for Issue 6.
- **Effort**: Small (< 1 hour)

### [Low] The two storage classes share no interface, so a caller cannot swap them

- **Category**: Maintainability
- **Location**: `eventbus/storage/memory.py:9-39`, `eventbus/storage/sqlite.py:9-77`, `eventbus/storage/__init__.py`
- **Problem**: `eventbus/storage/__init__.py` exports both classes as backends, but their method sets differ. `MemoryStorage` has `get_recent` and `clear`, and `SQLiteStorage` has neither. `SQLiteStorage` has `initialize` and `close`, and `MemoryStorage` has neither. Only `store`, `get_by_topic`, and `count` exist in both, and Issue 11 shows that one of those three returns a different order. No base class and no `typing.Protocol` records the contract, so a swap fails at run time with `AttributeError`. `MemoryStorage.count()` also disagrees with the data it holds: it returns `len(self._events)`, which the `maxlen` caps, while `_by_topic` keeps every event. So `count()` under-reports after the deque starts to evict.
- **Evidence**:
  ```python
  from .sqlite import SQLiteStorage
  from .memory import MemoryStorage
  ```
- **Suggested fix**: Define a `Storage` `Protocol` in `eventbus/storage/__init__.py` with the full method set. Add `initialize` and `close` to `MemoryStorage` as no-op coroutines. Add `get_recent` and `clear` to `SQLiteStorage`. Make `count()` report the true number of stored events.
- **Effort**: Small (< 1 hour)

### [Low] `DeduplicationMiddleware` measures its window with the wall clock

- **Category**: Correctness
- **Location**: `eventbus/middleware/dedup.py:21`, `eventbus/middleware/dedup.py:26`
- **Problem**: The class calls `time.time()`, which follows the system clock. An NTP correction or a manual clock change moves that clock. A backward step makes `now - ts` negative, so no entry expires and the cache holds every identifier until `max_seen` forces the eviction. A forward step expires every entry at once, so the middleware forgets identifiers that are still inside the window and passes duplicates. `time.monotonic()` never moves backward and is the correct clock for a duration.
- **Evidence**:
  ```python
  self._seen[event.event_id] = time.time()
  ...
  now = time.time()
  ```
- **Suggested fix**: Use `time.monotonic()` in both places. Keep `time.time()` for `Event.timestamp`, because that field records a wall-clock time on purpose.
- **Effort**: Small (< 1 hour)