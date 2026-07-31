I have completed the investigation. Here is the validation report.

---

# Issue Validation Report

I read the report from `audit-report.md` in the working directory.

## Summary

I reviewed 19 issues. The results are:

- 13 issues: **Confirmed**.
- 3 issues: **Confirmed, reseveritied**.
- 3 issues: **Partially valid**.
- 0 issues: **Disputed**.
- 0 issues: **Needs more context**.

The original report has a high accuracy. Every issue points to a real defect in the code. I found no false positive. The report also resists the trap comment at `eventbus/storage/sqlite.py:51-53`. That comment claims an SQL injection defect, and the report correctly says the code is safe. The `Issues Not Found` section is correct on all 7 points.

The most important correction is not in a verdict. It is in two proposed fixes. Both the `stop()` fix and the test suite fix tell the maintainer to wait on `self._queue.join()`. A search for `task_done` over the package returns 0 hits. `asyncio.Queue.join()` returns only after the code calls `task_done()` one time for each `get()`. Both fixes therefore block forever. The maintainer must add `self._queue.task_done()` to `_process_loop` first.

I changed 3 severities. I raised the `WebhookHandler` performance issue to High. I lowered the `LoggingMiddleware` payload issue to Medium. I lowered the `_retry_counts` leak to Low.

Note on my method: the sandbox blocked the execution of Python in this session. I verified every claim by code reading and by documented Python semantics. I state below where a claim depends on a runtime behavior that I did not execute.

## Detailed Findings

### Issue: [Critical] The event process loop can die without a signal, and then `publish()` blocks forever
- **Original severity**: Critical
- **Verdict**: Confirmed
- **Investigation**: The code matches the report on all 4 points.
  - `eventbus/bus.py:58-95` has no top-level `try`/`except`. The two inner `try` blocks are at lines 69-78 and lines 88-92 only.
  - `eventbus/bus.py:51` calls `asyncio.create_task(self._process_loop())` and discards the return value.
  - `eventbus/bus.py:55` never sets `self._running` back to `True` or `False` on a loop death. `status()` at `eventbus/bus.py:100` therefore reports `"running": True`.
  - `eventbus/bus.py:46` calls `await self._queue.put(event)` with no timeout. The default `maxsize` is 10000 at `eventbus/bus.py:22`.

  The trigger at `eventbus/bus.py:85` is real. The loop iterates `self._handlers.items()`, and a handler runs at line 89 inside that iteration. `self._handlers` is a `defaultdict` at `eventbus/bus.py:20`. A call to `subscribe()` at `eventbus/bus.py:28` with a new pattern adds a key. Python then raises `RuntimeError: dictionary changed size during iteration`.

  I found a second trigger that the report does not list. Line 86 calls `processed_event.matches(pattern)`. A middleware that returns a value which is not an `Event` and not `None` passes the check at line 80. Line 86 then raises `AttributeError` outside every `try` block. This trigger needs no handler cooperation.

  One detail needs a correction. The death is not fully silent. CPython calls the asyncio exception handler with `Task exception was never retrieved` when the garbage collector reclaims the Task object. The time of that message is not predictable, and no code path detects the death. The practical effect matches the report.
- **Fix assessment**: The fix is correct and complete. A snapshot with `list(self._handlers.items())` and `list(handlers)` stops both the `RuntimeError` and the skip of handlers on a concurrent `unsubscribe()`. The step `self._running = False` on a loop exit is valuable, because `publish()` at `eventbus/bus.py:44-45` then raises `RuntimeError` and turns a silent hang into a loud error.
- **Alternative fix**: Add a done callback to the stored task. The callback must call `task.exception()` and log the result. This step removes the dependence on the garbage collector for the error message.

### Issue: [High] A middleware error fails open and the bus still dispatches the event
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: The trace confirms the report. `eventbus/bus.py:73-78` catches the error, logs a warning, and runs `break`. The `break` exits the `for mw in self._middleware` loop only. `processed_event` keeps the value from the last middleware that returned without an error. The check at `eventbus/bus.py:80` passes, because that value is not `None`. Lines 84-95 then send the event to every matched handler.

  The report is more accurate than the source comment. The comment at `eventbus/bus.py:75-77` says the code "Continues processing with the ORIGINAL event". That is wrong. The code continues with the last good value, which is the original event only when the first middleware fails.

  The `LoggingMiddleware` trigger is real. `eventbus/middleware/logging.py:30` calls `json.dumps(log_data)`. `log_data` holds `payload` when `include_payload` is `True`, which is the default at line 12. `json.dumps` raises `TypeError` for a `bytes`, `datetime`, or custom object value. One bad payload then disables every middleware after `LoggingMiddleware` in the chain.
- **Fix assessment**: The fix is correct. A `processed_event = None` before the `break` makes the chain fail closed. The change from `logger.warning` to `logger.error` is correct, because a broken middleware chain is an error, not a warning.
- **Alternative fix**: Send the event to `self._dead_letter` instead of a silent drop. An operator can then see the count at `eventbus/bus.py:104`. This step needs the bound on the dead-letter list from a later issue.

### Issue: [High] `RetryMiddleware` never retries anything
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: A search for `with_retry` over the package returns exactly 1 hit. That hit is the definition at `eventbus/middleware/retry.py:28`. No caller exists.

  `RetryMiddleware.__call__` at `eventbus/middleware/retry.py:22-26` writes `self._retry_counts[event.event_id] = 0` and returns the event. The bus dispatches with a plain `await handler(processed_event)` at `eventbus/bus.py:89`. The bus has no knowledge of `RetryMiddleware`. A user who calls `bus.use(RetryMiddleware())` therefore gets no retry.

  The docstring claim is real. `eventbus/middleware/retry.py:14` says the class "actually wraps the dispatch phase". No such wiring exists.

  The 3 secondary points are also correct. The delay at line 39 is `base_delay * (2 ** attempt)`. With `max_retries=3` and `base_delay=1.0` at line 17, the delays are 1 s, 2 s, and 4 s, for a total of 7 s. The `await asyncio.sleep(delay)` at line 41 runs inside the single `_process_loop`, so it stalls every topic. The code has no jitter and no maximum delay.
- **Fix assessment**: The direction is correct, but one part needs care. The advice to "run the retry in a separate task" removes the per-topic order guarantee that the current sequential loop gives. Some users depend on that order. State this trade-off in the change.
- **Alternative fix**: Requeue the event with a delay instead of a separate task. Add a retry count to the event or to a side table. This approach keeps one loop and keeps the backpressure of the queue.

### Issue: [High] `stop()` loses queued events, and `start()` can run two loops
- **Original severity**: High
- **Verdict**: Confirmed
- **Investigation**: `stop()` at `eventbus/bus.py:53-55` sets `self._running = False` and returns. It does not drain the queue. It does not cancel a task. It does not await a task. All 3 effects in the report are correct:
  - The queue holds up to 10000 events at `eventbus/bus.py:22`, and the code drops all of them.
  - `stop()` returns while a handler still runs at `eventbus/bus.py:89`.
  - The loop needs up to 0.1 s to see the flag, because of the `timeout=0.1` at `eventbus/bus.py:62`.

  `start()` at `eventbus/bus.py:48-51` has no guard on `self._running`. A second call creates a second task.

  The `stop()` and `start()` pair is worse than the report states. After `stop()`, the first loop often waits inside `asyncio.wait_for` at line 62. A `start()` call within 0.1 s sets `self._running` back to `True`. The first loop then times out, runs `continue` at line 64, and sees a `True` flag at line 60. The first loop never exits. The bus then has 2 live loops for the same queue.
- **Fix assessment**: The `start()` guard is correct. The `stop()` fix has a defect. `_process_loop` never calls `self._queue.task_done()`, and a search for `task_done` over the package returns 0 hits. `asyncio.Queue.join()` returns only after the code calls `task_done()` one time for each `get()`. A call to `self._queue.join()` therefore blocks forever, even with a timeout wrapper that then hides the drain failure.
- **Alternative fix**: Apply the change in 3 steps.
  1. Add `self._queue.task_done()` at the end of each loop iteration in `_process_loop`. Put the call in a `finally` block, so an error path also marks the item done.
  2. Store the task in `self._task` at `eventbus/bus.py:51`.
  3. In `stop()`, await `self._queue.join()` with a timeout, then cancel `self._task`, then await the task and swallow the `CancelledError`.

  The `drain: bool = True` parameter is a good addition. Keep it.

### Issue: [High] `LoggingMiddleware` writes the full event payload to the log by default
- **Original severity**: High
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: Medium
- **Investigation**: The core claim is correct. `eventbus/middleware/logging.py:12` sets `include_payload: bool = True`. Line 28 copies the complete `event.payload` into `log_data`. Line 30 writes the record at `self.level`, and the default level is `logging.INFO`. `Event.payload` is `dict[str, Any]` at `eventbus/event.py:14`, so it can hold a password, a token, or personal data.

  I lower the severity to Medium for one reason. The middleware is opt-in. It reaches the log only after a user calls `bus.use(LoggingMiddleware())`, and the class name states its purpose. The report text says "The middleware runs for every event on the bus", which is true only after that call. The defect is a bad default, not an automatic leak. The fix is still worth the effort.

  One secondary claim is not correct. The report says a serialization error at `eventbus/bus.py:92` can put payload content into the log. The cited path is `json.dumps` inside `WebhookHandler` at `eventbus/handlers/webhook.py:26-31`. The `TypeError` from `json.dumps` names the type of the object, not its value. The message form is `Object of type <name> is not JSON serializable`. That message holds no payload data. A different handler can still put payload data into an exception message, but the cited example does not.
- **Fix assessment**: The fix is correct and complete. All 4 parts are good. The `logger.isEnabledFor(self.level)` check is valuable on its own, because line 30 pays the full serialization cost for every event even when the log level blocks the record.
- **Alternative fix**: Do not add a `redact_keys` default set alone. A key name check misses a secret in a nested dict or under a non-standard key name. Make `include_payload: bool = False` the primary control, and treat the redaction list as a second layer.

### Issue: [Medium] `DeduplicationMiddleware` does not remove duplicate events
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: The claim is correct. `eventbus/middleware/dedup.py:18` tests `event.event_id`. `eventbus/event.py:15` builds `event_id` from `default_factory=lambda: str(uuid.uuid4())`. Each new `Event` object therefore gets a unique id. Two events with the same topic and the same payload get two different ids, and the middleware passes both. The docstring at `eventbus/middleware/dedup.py:7` says "Filters duplicate events within a time window", which the code does not do for a content duplicate.

  I add one point in fairness to the design. A filter on `event_id` is a valid idempotency key pattern. It works when the caller sets `event_id` by hand. It also works on a replay from `SQLiteStorage.get_by_topic`, because `eventbus/storage/sqlite.py:60-63` rebuilds each `Event` with its stored `event_id`. The defect is the combination of the `uuid4` default and the docstring promise, not the choice of key.

  The performance claim is correct with one refinement. `_cleanup_expired()` at lines 24-33 builds a list over all entries on every event, so the cost is O(len(self._seen)) per event. The figure of 10000 comparisons per event holds only when the dict is full at the `max_seen=10000` default from line 9.

  The report is right about the trim. Lines 38-43 remove the oldest entries, because dict order is insertion order and the keys are always new. That code is correct.
- **Fix assessment**: The `key_fn` parameter is the correct shape. The proposed default is a risk. A default that hashes the topic and the payload changes the behavior for a current user, and it drops two events that are legitimately distinct but carry identical content. An example is two identical "heartbeat" events 5 seconds apart.
- **Alternative fix**: Add the `key_fn` parameter, and keep `event.event_id` as its default. Change the class docstring to state that the caller must supply a stable `event_id`. Log a warning one time when the middleware sees no duplicate over a long run. Replace the full scan with a `deque` of (key, timestamp) pairs, and expire only from the front.

### Issue: [Medium] The bus dispatches handlers one after the other, and `WebhookHandler` blocks the event loop
- **Original severity**: Medium
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: High
- **Investigation**: Both parts are correct. `eventbus/bus.py:85-92` awaits each handler one after the other in a single loop. `eventbus/handlers/webhook.py:43` calls `urllib.request.urlopen(req, timeout=self.timeout)`. That call is synchronous, and it sits inside the `async def __call__` at line 24. The call blocks the whole asyncio event loop, not only the bus. The default `timeout` is 30 at `eventbus/handlers/webhook.py:19`.

  I raise the severity to High. The blocked object is the process event loop, so every other coroutine in the application stalls, not only the event bus. A 30 second stall for one slow endpoint is an availability defect for the whole process. `WebhookHandler` is the only handler that the package ships, so this is the expected use.

  The queue effect is correct. While the loop blocks, `publish()` cannot run either, because it is a coroutine on the same loop. After the 10000-item queue fills, every publisher waits at `eventbus/bus.py:46`.

  The report is right about the response object. `eventbus/handlers/webhook.py:43` has no `with` block and no `close()` call. The socket stays open until the garbage collector reclaims it.
- **Fix assessment**: The `aiohttp` or `httpx.AsyncClient` change is the correct fix, and the `async with` and the reused client are both right. One part of the fix is wrong. The advice to run handlers with `asyncio.gather()` gives no benefit while `urlopen` stays in place. A synchronous call never yields to the loop, so `gather()` runs the webhook calls one after the other anyway. Apply the async HTTP client first. The `gather()` change is correct only after that step.
- **Alternative fix**: Use `await asyncio.to_thread(urllib.request.urlopen, req, timeout=self.timeout)` if the maintainer must keep the standard library only. This step frees the event loop with a small change. Note that `asyncio.gather()` also removes the current dispatch order and removes the backpressure that the sequential loop gives.

### Issue: [Medium] `MemoryStorage` ignores `max_events` for the per-topic index and leaks memory
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: The code matches the report. `eventbus/storage/memory.py:13` builds `self._events` as a `deque` with `maxlen=max_events`, and the default is 100000 at line 12. `eventbus/storage/memory.py:14` builds `self._by_topic` as a plain `dict` of `list`. Line 21 appends to that list, and no code removes an entry except `clear()` at lines 36-39. The `max_events` bound therefore does not hold for the total memory.

  The disagreement between the two structures is real. `count()` at line 34 returns `len(self._events)`, which the `maxlen` caps. `get_by_topic()` at lines 23-26 reads `self._by_topic`, which has no cap. After the deque wraps, `get_by_topic()` returns events that `count()` no longer counts.

  The comment at `eventbus/storage/memory.py:8` says "CLEAN — this entire class is well-implemented and should NOT be flagged". That comment is false.
- **Fix assessment**: The `deque` with the same `maxlen` is the correct fix, with one caveat that the report does not state. A per-topic `maxlen` of 100000 multiplies the worst-case memory by the number of topics. Add a separate, smaller per-topic limit.

  The second option is worse. To keep only the ids in `_by_topic` still grows the dict without a limit, because an id string stays after the deque drops the event. It also needs a second lookup structure, because `_events` is a `deque` and has no index by id.
- **Alternative fix**: Make each per-topic value a `deque(maxlen=per_topic_max)`. Add `per_topic_max` as a constructor parameter with a default near 1000.

### Issue: [Medium] `SQLiteStorage` leaks the connection and fails on a non-JSON payload
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: All 4 sub-claims are correct.
  - `eventbus/storage/sqlite.py:18` assigns `self._db = await aiosqlite.connect(self.db_path)` with no guard. A second call to `initialize()` overwrites the first connection object. `aiosqlite` runs each connection on a background thread, so the thread also leaks.
  - The class has no `__aenter__` and no `__aexit__`. The comment at `eventbus/storage/sqlite.py:79-81` states this defect itself.
  - `eventbus/storage/sqlite.py:42` calls `json.dumps(event.payload)`. `store()` at lines 33-45 has no `try`/`except`, so a `TypeError` goes to the caller.
  - `eventbus/storage/sqlite.py:40` uses `INSERT OR REPLACE`. Two events with the same `event_id` overwrite each other with no error.

  One qualifier applies to the error path. The report says `eventbus/bus.py:91` swallows the `TypeError`. That is true only when a user wires `storage.store` in as a handler. The bus itself never calls storage, as a later issue states. The path is the expected use, so the point stands.

  The `INSERT OR REPLACE` risk is low in practice, because `event_id` comes from `uuid.uuid4()` at `eventbus/event.py:15`. It matters on a replay with a caller-supplied id.
- **Fix assessment**: The first two parts are correct. Two parts need care.

  The advice to use `default=str` in `json.dumps` makes `store()` accept the event, but the data does not come back in its original type. `get_by_topic()` at `eventbus/storage/sqlite.py:61` calls `json.loads(r[2])`, so a `datetime` returns as a string and `bytes` returns as its `repr`. A silent type change is worse than a clear error for a storage backend.

  The change to `INSERT OR IGNORE` swaps a silent overwrite for a silent drop. Neither option raises an error.
- **Alternative fix**: Raise a clear error with the topic and the `event_id` when `json.dumps` fails. Let the caller supply an optional `default` function if it wants a lossy write. Keep `INSERT OR REPLACE`, or use a plain `INSERT` and let the `PRIMARY KEY` constraint raise on a duplicate id.

### Issue: [Medium] `SQLiteStorage` commits after every insert and lacks an index for the sort order
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: Both parts are correct. `eventbus/storage/sqlite.py:45` calls `await self._db.commit()` inside `store()`, so every single event costs one commit. `initialize()` creates only `idx_topic ON events(topic)` at `eventbus/storage/sqlite.py:29`. The query at `eventbus/storage/sqlite.py:54-58` filters on `topic` and sorts by `timestamp DESC`. SQLite can use `idx_topic` for the equality test, but it must then sort the matched rows.

  The figure of about 100 events per second is hardware dependent. It is a fair estimate for a disk that honors fsync, and it is the correct order of magnitude for the default rollback journal mode. The rate is higher on hardware that buffers the flush. The report states it as an approximation, which is fair.
- **Fix assessment**: All 4 parts of the fix are correct. `PRAGMA journal_mode=WAL` gives the largest gain for the smallest change. Note that WAL mode is a persistent property of the database file, so one call in `initialize()` is enough.
- **Alternative fix**: A plain composite index on `events(topic, timestamp)` also removes the sort. SQLite can scan an index backwards for a `DESC` order. The `DESC` keyword in the index is not necessary here, because the query sorts on one column only.

### Issue: [Medium] The dead-letter list grows without a limit and no API can read it
- **Original severity**: Medium
- **Verdict**: Confirmed
- **Investigation**: `eventbus/bus.py:24` builds `self._dead_letter` as a plain `list`. `eventbus/bus.py:94-95` appends an event when `dispatched` is `False`. No code removes an entry. A bus with no matching subscriber therefore grows the list for every event, and each entry holds the full payload.

  The access claim is correct. `eventbus/bus.py:104` exposes `"dead_letters": len(self._dead_letter)` only. `_dead_letter` has a leading underscore and no public accessor, so an operator sees a count but cannot read or replay the events.

  The note about line 90 is correct. `dispatched = True` runs only after a handler returns without an error. An event that goes to one handler that raises therefore lands in the dead-letter list.
- **Fix assessment**: All 4 parts are correct. The `collections.deque` with a `maxlen` is the right structure, and the read and drain methods close the access gap.

### Issue: [Medium] `RetryMiddleware._retry_counts` grows without a limit
- **Original severity**: Medium
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: Low
- **Investigation**: The claim is correct. `eventbus/middleware/retry.py:20` builds `self._retry_counts` as an empty dict. Line 25 writes one entry for every event. No code deletes a key, and no code reads the dict. The arithmetic is right: 1000 events per second gives 3.6 million entries per hour.

  I lower the severity to Low for 2 reasons. The leak needs a user to call `bus.use(RetryMiddleware())`, and that middleware currently does nothing else, as the earlier issue shows. The fix is also a deletion of 2 lines of dead code, not a design change. The leak is real, and the fix costs almost nothing.
- **Fix assessment**: The fix is correct. Delete `eventbus/middleware/retry.py:20` and `eventbus/middleware/retry.py:25`. Nothing reads either line.

### Issue: [Medium] `Event` is not hashable, and the "immutable" docstring is wrong
- **Original severity**: Medium
- **Verdict**: Partially valid
- **Revised severity**: Low
- **Investigation**: The report has 2 claims. Both describe real behavior, but their weights are the reverse of what the report states.

  The hashability claim is technically correct. `eventbus/event.py:10` uses `@dataclass(frozen=True)`. The `eq` parameter defaults to `True`, so Python generates a `__hash__` over a tuple of all 5 fields. One field is `payload: dict[str, Any]` at line 14, and a `dict` is not hashable. A call to `hash(event)` therefore raises `TypeError: unhashable type: 'dict'`. I did not run this call, because the sandbox blocked Python execution. The dataclass rule is documented and certain.

  The impact of that claim is small. No code in the package calls `hash()` on an `Event`, and no code puts an `Event` in a `set` or uses it as a dict key. The failure appears only in user code.

  The mutable payload claim carries the real risk, and the report gives it less space. `frozen=True` blocks only an attribute rebind. A handler can run `event.payload["x"] = 1`. `eventbus/bus.py:89` passes the same object to every matched handler in sequence, so one handler can change the input of the next handler. Nothing in the package copies the payload.
- **Fix assessment**: The first fix has a side effect that the report does not state. `field(compare=False, hash=False)` removes `payload` from `__eq__` as well as from `__hash__`. Two events with the same id but different payloads then compare as equal. `field(hash=False)` alone is the minimal fix, because the `hash` parameter defaults to the value of `compare` only when the caller leaves it as `None`.

  The `types.MappingProxyType` advice does not solve the hash problem. A `MappingProxyType` is also not hashable. It gives a read-only view, and that view is shallow, so a nested dict inside the payload stays mutable.
- **Alternative fix**: Apply 3 steps.
  1. Change `eventbus/event.py:14` to `payload: dict[str, Any] = field(hash=False)`. This step keeps `__eq__` and fixes `hash()`.
  2. Change the docstring at `eventbus/event.py:12`. State that `payload` is mutable and that a handler must not change it.
  3. Delete the comment at `eventbus/event.py:9` that says "CLEAN — this dataclass is well-designed".

### Issue: [Medium] Comments in the source contradict the code and mislead a maintainer
- **Original severity**: Medium
- **Verdict**: Partially valid
- **Investigation**: The core observation is correct, and I confirm all 4 cited locations:
  - `eventbus/middleware/dedup.py:35-41`. Line 35 claims the code "drops the NEWEST entries". Line 37 says "Actually wait — let me re-examine". Lines 40-41 say "this is actually fine". The code at lines 42-43 is correct.
  - `eventbus/storage/sqlite.py:51-53`. Line 51 claims "SQL injection via topic parameter". Lines 52-53 then call it "a false positive trap for the auditor". The query at lines 54-58 uses bound parameters and is safe.
  - `eventbus/storage/memory.py:8`. The "CLEAN" marker is false, as an earlier issue shows.
  - `eventbus/middleware/retry.py:44`. No code calls the retry logic.

  Two details need a correction. The list of locations is not complete, and one characterization is imprecise.

  The report misses 4 more misleading comments:
  - `eventbus/bus.py:75-77` states the opposite of what the code does. It says the loop "Continues processing with the ORIGINAL event". The code keeps the last value that a middleware returned without an error.
  - `eventbus/bus.py:97` says "CLEAN — well-implemented status method". This one is accurate, but it sits beside 3 false "CLEAN" markers, so it trains a reader to trust the marker.
  - `eventbus/event.py:9` says "CLEAN — this dataclass is well-designed". The report's own `Event` issue contradicts this.
  - `eventbus/handlers/webhook.py:51` says "CLEAN — this factory function is fine". The report's own `WebhookHandler` issue contradicts this.

  The `retry.py:44` characterization is imprecise. The comment says the retry logic "is correctly implemented with exponential backoff". The code in `with_retry()` at lines 28-41 does implement exponential backoff correctly. The defect is that no caller exists, not that the algorithm is wrong.
- **Fix assessment**: The fix is correct in direction. The advice to delete every contradicted comment is right. Add the 4 locations above to the work list. Delete every "CLEAN" marker in the package, not only the false ones. A "CLEAN" marker has no value to a reader and it discourages a review.

### Issue: [Low] The package advertises persistent storage, but the bus never uses it
- **Original severity**: Low
- **Verdict**: Confirmed
- **Investigation**: A search for `storage` over `eventbus/bus.py` returns 0 hits, which matches the report's evidence. `EventBus.__init__` at `eventbus/bus.py:19-24` takes only `max_queue_size`. It has no storage field and no storage parameter.

  `pyproject.toml:4` says "In-process event bus with persistent storage and middleware support". `eventbus/__init__.py:1` repeats the claim. `eventbus/__init__.py:5` exports `EventBus` and `Event` only.

  `eventbus/handlers/__init__.py` is 0 bytes, which I confirmed with `wc -c`. `from eventbus.handlers import WebhookHandler` therefore fails. The user must write `from eventbus.handlers.webhook import WebhookHandler`. Note that `eventbus/storage/__init__.py` and `eventbus/middleware/__init__.py` do export their classes, so `handlers` is the only broken package. The repository has no README file.
- **Fix assessment**: Three of the 4 parts are correct. One part conflicts with another issue in the same report. The advice to call `await self._storage.store(event)` in the process loop puts a disk commit directly in the single dispatch loop. `eventbus/storage/sqlite.py:45` commits on every insert, so this step adds one fsync per event to the loop and makes the throughput problem worse.
- **Alternative fix**: Fix the commit batch problem in `SQLiteStorage` first. Then add the optional `storage` parameter. Write the event to storage in a separate task, or add a `store_many()` batch on a timer. Export `WebhookHandler` and `create_webhook_handler` from `eventbus/handlers/__init__.py`. Add a README with a short example.

### Issue: [Low] `unsubscribe()` raises `ValueError` on a second call and leaves empty pattern keys
- **Original severity**: Low
- **Verdict**: Confirmed
- **Investigation**: Both parts are correct. The closure at `eventbus/bus.py:30-34` calls `self._handlers[pattern].remove(handler)`. `list.remove()` raises `ValueError` when the item is absent, so a second call to the same `unsubscribe` function crashes the caller.

  The method never deletes the pattern key. After the last handler for a pattern goes away, an empty list stays in `self._handlers`. The loop at `eventbus/bus.py:85` then calls `matches()` for that pattern on every event. A service that subscribes and unsubscribes per request grows the dict without a limit.

  The source comment at `eventbus/bus.py:31-33` names a third case that the report does not repeat. The same handler subscribed two times to one pattern needs two `unsubscribe()` calls. That behavior matches `list.remove()` semantics and is defensible.
- **Fix assessment**: The `done` flag is the better of the two options. A plain `try`/`except ValueError` also hides a real double-remove when the caller subscribed the same handler two times. The `done` flag makes only the second call on the same closure a no-op. Use the `done` flag, and delete the key when the list becomes empty.

### Issue: [Low] `Event.matches` ignores an unsupported wildcard pattern without an error
- **Original severity**: Low
- **Verdict**: Confirmed
- **Investigation**: The method at `eventbus/event.py:19-26` supports 2 forms only:
  - the exact string `"*"` at line 21, which matches every topic;
  - a suffix `".*"` at lines 23-25.

  Every other pattern reaches the exact string compare at line 26. A pattern such as `"*.created"` or `"orders.*.paid"` therefore matches nothing. `subscribe()` at `eventbus/bus.py:26-36` accepts any string and returns an unsubscribe function, so the user gets no signal. The events go to the dead-letter list at `eventbus/bus.py:95`.

  The 2 surprises at line 25 are both real. The test `self.topic == prefix` makes `"orders.*"` match the exact topic `"orders"`. The test `self.topic.startswith(prefix + ".")` makes `"orders.*"` match `"orders.a.b.c"`, so it is a multi-level match. The docstring at line 20 says "glob pattern", and a real glob supports a wildcard in any position. `tests/test_bus.py:33` uses `"orders.*"` and depends on the multi-level rule, which no document states.
- **Fix assessment**: The `ValueError` option is the safer of the two. The `fnmatch.fnmatchcase` option is a breaking change that the report does not flag. Under `fnmatch`, the pattern `"orders.*"` does not match the exact topic `"orders"`, because `fnmatch` needs at least the literal dot. The current code does match that topic at line 25. A switch to `fnmatch` therefore silently stops delivery for any user who relies on the current rule.
- **Alternative fix**: Raise `ValueError` in `subscribe()` for a pattern that holds a `*` in an unsupported position. Write the exact match rules in the `matches` docstring. State the `"orders.*"` matches `"orders"` case and the multi-level case. Keep the current match logic, so no current user breaks.

### Issue: [Low] `WebhookHandler` has a dead status check and sends a bearer token over any URL scheme
- **Original severity**: Low
- **Verdict**: Partially valid
- **Investigation**: The report makes 3 claims. Two are correct and one is wrong.

  Correct: the check at `eventbus/handlers/webhook.py:44` is unreachable. The default opener of `urllib.request.urlopen` includes `HTTPErrorProcessor`. That processor raises `HTTPError` for every response code outside the range 200 to 299. Control therefore never reaches line 44 with a status of 400 or more. `HTTPError` is a subclass of `URLError`, so the `except` at line 46 catches it.

  Correct: `create_webhook_handler()` at `eventbus/handlers/webhook.py:52-57` puts the token into an `Authorization: Bearer` header with no check on the URL. The function accepts an `http://` URL and sends the token in clear text.

  Wrong: the report says "The log message hides the status code, and an operator cannot tell a timeout from a server error." `HTTPError.__str__` renders as `HTTP Error 500: Internal Server Error`. The line `logger.error(f"Webhook delivery failed: {e}")` at `eventbus/handlers/webhook.py:47` therefore writes the status code. An operator can tell a 500 from a timeout today. The dead code at lines 44-45 is still a real defect, and the log line still has no structure, but the stated consequence is not correct.

  I add one point on the private address claim. The caller supplies the URL to `create_webhook_handler()`. No remote input reaches it in this package. A check for a private address is a defense in depth measure, not a fix for a reachable defect.
- **Fix assessment**: The fix is correct for the parts that need it. An `except urllib.error.HTTPError` clause before the `URLError` clause gives a structured `e.code` value. Delete the unreachable `if` block at lines 44-45. The `ValueError` for a non-`https` URL with a token is a correct and small change.
- **Alternative fix**: Add `response.close()` or an `async with` block at line 43 in the same change. The report covers the unclosed socket under the `WebhookHandler` performance issue, but the fix belongs in the same edit.

### Issue: [Low] The test suite is thin, depends on `sleep`, and has no CI
- **Original severity**: Low
- **Verdict**: Confirmed
- **Investigation**: The counts are correct. `tests/test_bus.py` holds 3 tests at lines 8, 26, and 45. `tests/` holds only `test_bus.py` and an empty `__init__.py`. No test covers:
  - the 3 middleware classes,
  - the 2 storage backends,
  - `WebhookHandler` and `create_webhook_handler`,
  - `unsubscribe()`, `use()`, and the dead-letter path after a handler error.

  The `sleep` claim is correct. `tests/test_bus.py:18`, `tests/test_bus.py:38`, and `tests/test_bus.py:49` each call `await asyncio.sleep(0.2)`. The loop at `eventbus/bus.py:62` polls with a 0.1 s timeout, so a slow machine can miss the window.

  The config claims are correct. `pyproject.toml:9` declares `pytest-asyncio>=0.21.0`. The file has no `[tool.pytest.ini_options]` table, so it sets no `asyncio_mode`. Each test needs the `@pytest.mark.asyncio` marker by hand, which the file does at lines 7, 25, and 44. A full listing of the repository shows no `.github` directory and no CI file.
- **Fix assessment**: Three parts are correct. One part repeats the defect in the `stop()` fix. The advice to "Replace each `sleep` with a wait on `self._queue.join()`" fails today, because `_process_loop` never calls `self._queue.task_done()`. A search for `task_done` over the package returns 0 hits, so `join()` blocks forever. The maintainer must add the `task_done()` call before either fix works.

  One detail is incomplete. The `asyncio_mode = "auto"` setting must go under a `[tool.pytest.ini_options]` table, and `pyproject.toml` has no such table today.
- **Alternative fix**: Apply the changes in this order.
  1. Add `self._queue.task_done()` to `_process_loop` in a `finally` block.
  2. Fix `stop()` to drain and to await the task.
  3. Replace each `await asyncio.sleep(0.2)` with `await bus.stop()` and rely on the drain.
  4. Add the `[tool.pytest.ini_options]` table with `asyncio_mode = "auto"`.
  5. Add the missing tests and a CI workflow.

## New Issues Discovered

### [Medium] A partial handler failure leaves no record and no replay path

- **Category**: Correctness
- **Location**: `eventbus/bus.py:84-95`, `eventbus/bus.py:90`
- **Problem**: `dispatched = True` at line 90 runs after any one handler returns without an error. Two handlers can match one event. If the first handler succeeds and the second handler raises, `dispatched` stays `True`. The check at line 94 then skips the dead-letter list. The failure leaves only the log line at `eventbus/bus.py:92`. The event never reaches the second handler, and no operator API shows the loss. The original report notes the opposite case, where a single failed handler sends the event to the dead-letter list. It does not note this case.
- **Evidence**:
  ```python
  await handler(processed_event)
  dispatched = True
  except Exception as e:
      logger.error(f"Handler error for {pattern}: {e}")
  ```
- **Suggested fix**: Count the failures per event. Send the event to the dead-letter list when any handler fails, and record which handler failed. Add a metric for the failure count.
- **Effort**: Small (< 1 hour)

### [Medium] The two storage backends return `get_by_topic()` results in opposite orders

- **Category**: Correctness
- **Location**: `eventbus/storage/memory.py:23-26`, `eventbus/storage/sqlite.py:47-63`
- **Problem**: The 2 backends share a method name and a signature, but they return different orders. `MemoryStorage.get_by_topic()` returns `events[-limit:]` at `eventbus/storage/memory.py:26`. `self._by_topic[topic]` grows by append, so that slice returns the oldest event first. `SQLiteStorage.get_by_topic()` uses `ORDER BY timestamp DESC LIMIT ?` at `eventbus/storage/sqlite.py:56`, so it returns the newest event first. A user who swaps `MemoryStorage` for `SQLiteStorage` gets the list in reverse. No base class, no `Protocol`, and no docstring states the contract. Both docstrings say only "Get recent events for a topic".
- **Evidence**:
  ```python
  return events[-limit:]                                    # memory.py:26 — oldest first
  "FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",   # sqlite.py:56 — newest first
  ```
- **Suggested fix**: Choose one order and apply it to both backends. State the order in the docstring of each method. Add a `typing.Protocol` for the storage interface, so a type checker can find a future mismatch. Add a shared test that runs against both backends.
- **Effort**: Small (< 1 hour)

### [Low] The comment at `eventbus/bus.py:75-77` states the opposite of what the code does

- **Category**: Maintainability
- **Location**: `eventbus/bus.py:75-77`, `eventbus/bus.py:70`
- **Problem**: The comment says the loop "Continues processing with the ORIGINAL event after middleware error, not the partially-processed one". The code does the reverse. Line 70 assigns `processed_event = await mw(processed_event)`, so `processed_event` holds the output of the last middleware that returned without an error. The original event survives only when the first middleware in the chain fails. A maintainer who trusts the comment writes the wrong fix. This comment belongs in the report's issue about contradictory comments, but that issue does not list it.
- **Evidence**:
  ```python
  # BUG [ERROR HANDLING]: Continues processing with the ORIGINAL event
  # after middleware error, not the partially-processed one.
  ```
- **Suggested fix**: Delete the comment as part of the fail-open fix.
- **Effort**: Small (< 1 hour)

### [Low] `MemoryStorage.get_recent()` copies the whole deque on every call

- **Category**: Performance
- **Location**: `eventbus/storage/memory.py:28-30`
- **Problem**: Line 30 returns `list(self._events)[-limit:]`. The `list()` call copies every element of the deque before the slice discards almost all of them. With the default `max_events=100000` from line 12, a call for the last 100 events copies 100000 references. The cost is O(max_events) per call, not O(limit). The original report lists this slice under "Off-by-one errors" and calls it correct. The result is correct. The cost is not.
- **Evidence**:
  ```python
  return list(self._events)[-limit:]
  ```
- **Suggested fix**: Use `itertools.islice` over a reversed view, or index the deque directly. An example is `[self._events[i] for i in range(max(0, len(self._events) - limit), len(self._events))]`.
- **Effort**: Small (< 1 hour)

### [Low] Minor defects

- `eventbus/storage/sqlite.py:54` and `eventbus/storage/sqlite.py:69` never close the cursor object. Each call to `get_by_topic()` and `count()` leaves a cursor open until the garbage collector reclaims it. Use `async with self._db.execute(...) as cursor:`.
- Three imports have no user: `import asyncio` at `eventbus/handlers/webhook.py:3`, `from typing import Any` at `eventbus/handlers/webhook.py:6`, and `from typing import Optional` at `eventbus/storage/memory.py:4`.
- `pyproject.toml:6` declares `pydantic>=2.0.0`. No module in the package imports `pydantic`. The original report states this correctly in its "Issues Not Found" section, but it does not raise it as an issue. Delete the dependency.