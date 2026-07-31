I have everything I need. All load-bearing claims are now verified against source (including the CPython stdlib). Python execution is blocked in this sandbox, so the two runtime-behavior claims (test flakiness, pytest-asyncio mode) are assessed from stdlib/plugin semantics rather than an actual run — I flag that where it matters.

# Issue Validation Report

*Report read from `./audit-report.md` (34 KB, 17 findings + an "Issues Not Found" section) against the `eventbus` package in the same directory.*

## Summary

**17 findings reviewed: 14 confirmed (1 reseveritied upward), 3 partially valid, 0 outright disputed.**

This is an unusually accurate report. Every file/line citation I checked resolved correctly, and the three stdlib claims — `HTTPRedirectHandler.redirect_request` dropping the body and copying `Authorization`, `HTTPErrorProcessor` raising on non-2xx, `HTTPError` subclassing `URLError` — check out verbatim against `python3.13/urllib/request.py:594-656` and `urllib/error.py:35-50`. The reporter also correctly refused to trust the in-source `BUG`/`CLEAN` annotations, which is the single most valuable judgment call in the document: the two classes marked `CLEAN` do contain real defects, and the two loudest `BUG` markers are non-issues.

The weaknesses are consistent in shape — **the observations are right, some conclusions over-reach**:

- Finding 1 claims the fatal exception is "never logged." It is logged, via asyncio's *"Task exception was never retrieved."*
- Finding 9 claims dedup "can never deduplicate anything." It works fine if the caller passes `event_id` explicitly, which is a public dataclass field.
- Finding 12 inherits a wrong claim straight from the in-source comment it elsewhere criticizes (duplicate-subscription removal is *correct* behavior).
- Finding 17's flakiness mechanism and its pytest-asyncio claim are both wrong.
- **Finding 3's proposed fix would hang forever**: it recommends `await self._queue.join()`, but `_process_loop` never calls `task_done()` (grep-verified: zero occurrences in the package). Applying that fix as written converts a data-loss bug into a permanent shutdown deadlock.

Deployment context matters for triage: `pyproject.toml:4` advertises "persistent storage and middleware support" at version `1.3.0`, but grep confirms the storage backends and `with_retry` are unreachable. So roughly a third of the flagged code — findings 8, 10, and the SQLite half of others — is **dead code today**. Real as library API defects, zero production impact until someone wires them up. I note this per-finding rather than downgrading, since a v1.3.0 published package is expected to have working exports.

I also found **5 issues the report missed**, including one on the dataclass it correctly declined to trust and one that invalidates its own recommended fix.

## Detailed Findings

### Issue 1: Subscribing during dispatch kills the processing loop, which then fails silently and deadlocks `publish()`
- **Original severity**: CRITICAL
- **Verdict**: Confirmed (two details corrected; proposed fix contains a landmine)
- **Investigation**: The three-part chain is real.

  (1) `bus.py:85` iterates `self._handlers.items()` while awaiting handlers at `bus.py:89`. If a handler subscribes a *new* pattern, the dict size changes and the next `__next__` raises `RuntimeError`. I confirmed the ordering matters here: in CPython's `dictiter_iternextitem`, the `di_used != ma_used` size check precedes the exhaustion check, so the error fires even when the mutation happens during what would have been the final iteration. The raise originates in the `for` statement itself, outside the `try` at `bus.py:88-92`, and `_process_loop` has no outer handler — so it propagates and terminates the task. Confirmed.

  (2) `bus.py:51` discards the `create_task` handle. Confirmed.

  (3) `_running` is never cleared on abnormal exit, so `publish()` passes its guard at `bus.py:44` and `await self._queue.put(event)` at `bus.py:46` succeeds until 10 000 events accumulate, then blocks with no timeout and no consumer. `status()["running"]` still reports `True`. Confirmed — this is the part that earns Critical.

  **Correction 1**: "the `RuntimeError` is never logged and never surfaces" is wrong. When the orphaned Task is garbage-collected with an unretrieved exception, `Task.__del__` invokes the loop's exception handler and asyncio logs `Task exception was never retrieved` at ERROR level *with a full traceback*. Since `start()` drops the only reference and the loop releases its own on completion, CPython refcounting collects it promptly. The real debugging story is "one ERROR traceback, then permanent silence" — not "zero log lines." Still bad, but materially easier to diagnose than claimed.

  **Correction 2**: the cited asyncio-docs warning that an unreferenced task "can vanish mid-execution" doesn't apply while the task is pending — the event loop holds a reference through the future it awaits. Not load-bearing.

  On the inner-loop hazard: the report is right that `unsubscribe()` from inside a handler mutates the list under `for handler in handlers` and silently skips the next handler. Worth adding that this does *not* raise, because the dict size is unchanged — so a self-cancelling one-shot handler (arguably a more common pattern than lazy child subscription) hits the silent-skip path, not the crash path. It only bites with ≥2 handlers on the same pattern.

  I'd also reframe the severity rationale: the specific trigger is narrower than "a normal pattern," but sub-defects (2) and (3) mean **any** fatal error in the loop body becomes an invisible permanent outage. `processed_event.matches(pattern)` at `bus.py:86` is also outside any try — a non-`str` pattern is enough. The supervision gap is the Critical part, independent of trigger.
- **Fix assessment**: Snapshot iteration, `self._task` + done-callback that logs and clears `_running`, and a `try/except Exception` around the loop body are all correct and I'd apply them as written. **But the `publish()` half needs care**: an unconditional `asyncio.wait_for` on `put` changes `publish()`'s contract from "blocks under backpressure" to "raises `TimeoutError`," which will break existing callers that have no handler. Make it opt-in.
- **Alternative fix**: Split this into two changes. First, land the supervision fix alone (task handle + done-callback clearing `_running` + loop-body try/except) — small, no API change, and it converts every variant of this failure into a loud one. Then treat backpressure separately: add a `publish_timeout: float | None = None` constructor arg defaulting to today's blocking behavior, so opting into `QueueFull` is a caller decision. Also have the done-callback set an `asyncio.Event` so `status()` can distinguish "running" from "worker alive."

### Issue 2: Synchronous `urllib` in an async handler blocks the event loop
- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: `webhook.py:43` is a blocking `urlopen` inside `async def __call__`, with `timeout: int = 30` from `webhook.py:19`. One unresponsive endpoint stalls the whole process event loop for 30 s — every coroutine in the host application, not just the bus. The amplification claim also holds: `bus.py:85-92` awaits handlers serially in nested `for` loops with no `gather`, so throughput is one webhook round-trip per event across all topics. The note that `asyncio` is imported at `webhook.py:3` and never used is correct (I confirmed by grep; `typing.Any` at `webhook.py:6` and `typing.Optional` at `memory.py:4` are likewise unused).
- **Fix assessment**: Correct. `asyncio.to_thread` is the right stopgap and `httpx.AsyncClient` the right destination — and per Issue 6, `httpx`'s `follow_redirects=False` default fixes the redirect finding for free, which is a genuine argument for doing the migration rather than the stopgap.
- **Alternative fix**: One caution on "dispatch handlers concurrently via `asyncio.gather`": that silently changes delivery semantics from ordered-serial to unordered-concurrent, and combined with the shared-mutable-payload issue (New Issue B below) it introduces real interleaving hazards for handlers that mutate `event.payload`. Fix the payload sharing first, or gate concurrency behind a `concurrent_dispatch=False` flag.

### Issue 3: `stop()` silently discards queued events and never awaits the worker
- **Original severity**: HIGH
- **Verdict**: Confirmed — **but the proposed fix is broken**
- **Investigation**: `bus.py:53-55` sets `_running = False` and returns. The worker notices only at the next `while` check, so up to ~100 ms later, and up to 10 000 queued events are dropped with no log line. No completion barrier: `stop()` can return while the worker is mid-`await handler(...)`, so a following `SQLiteStorage.close()` races a handler still using the connection. All confirmed.
- **Fix assessment**: **`await self._queue.join()` would hang forever.** `join()` blocks until `_unfinished_tasks` reaches zero, which only happens via `task_done()` — and `task_done` appears nowhere in the package (grep-verified). `_process_loop` calls `self._queue.get()` at `bus.py:62` and never acknowledges. So every event ever published leaves `_unfinished_tasks` permanently incremented, and the recommended fix turns silent data loss into a guaranteed shutdown deadlock. The `wait_for`-plus-sentinel and `__aenter__`/`__aexit__` suggestions are good.

  The "10 needless loop wakeups per second forever" point is technically true but negligible (a timer callback on an idle loop); it reads as padding in a finding that already stands on data loss.
- **Alternative fix**: Either add `self._queue.task_done()` in a `finally` after dispatch *before* using `join()`, or skip `join()` entirely and drain directly, which needs no bookkeeping change:
  ```python
  async def stop(self, drain_timeout: float = 5.0) -> None:
      self._accepting = False                    # reject new publishes
      try:
          await asyncio.wait_for(self._drained.wait(), drain_timeout)
      except asyncio.TimeoutError:
          logger.warning("stop() dropping %d undelivered events", self._queue.qsize())
      self._running = False
      await self._queue.put(_SENTINEL)           # wake a blocked getter
      if self._task:
          await self._task
  ```
  Note this needs `_accepting` as a flag separate from `_running`: reusing `_running` for both "accepting publishes" and "worker should continue" is what makes the current shutdown unorderable, and it's the same conflation that makes Issue 1's dead-worker state undetectable. Fixing that one overload improves both findings.

### Issue 4: `RetryMiddleware` performs no retries and leaks one dict entry per event
- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Grep-verified — `with_retry` occurs exactly once in the package, at its own definition (`retry.py:28`). Nothing in `bus.py` provides a handler-wrapping hook. So `bus.use(RetryMiddleware())` installs a pass-through whose entire effect is `self._retry_counts[event.event_id] = 0` at `retry.py:25`. `_retry_counts` is written per event and never read or deleted anywhere. Confirmed: unbounded growth, and no retries despite the class name, docstring, and the `# CLEAN — the retry logic above is correctly implemented` stamp at `retry.py:44`.

  High is right, and for the reason the report gives: the failure mode is operator belief, not just wasted memory. A team reading `bus.use(RetryMiddleware())` in their startup code reasonably concludes failed webhook deliveries retry with backoff; in reality `bus.py:92` logs once and the event is gone (and per Issue 7, not even dead-lettered if a sibling handler succeeded).

  The leak arithmetic is *understated*, not overstated: at 1 000 events/s, 3.6 M entries/hour with 36-char UUID string keys is roughly 185 bytes/entry all-in (str object + dict slot), so ~650 MB/hour rather than ~350 MB. Directionally the same conclusion.
- **Fix assessment**: Sound, and correctly identifies that the root cause is a missing extension point rather than a bug in `with_retry` (the backoff logic itself is fine). Deleting `_retry_counts` is right — it has no reader.
- **Alternative fix**: Prefer the documentation option over adding `bus.wrap()`. `with_retry(handler, event)` already has decorator shape; documenting it as something users apply to their own handlers ships today with no new bus API, no interaction with the middleware chain, and no ambiguity about whether retries happen before or after dedup. If `bus.wrap()` is added instead, its ordering relative to `use()` middleware becomes a design question that needs answering explicitly. Separately: while `range(self.max_retries + 1)` is correct, add the max-delay cap — with `base_delay=1.0` and `max_retries=3` the final sleep is 4 s, but a user setting `max_retries=10` gets a 512 s sleep inside the single-threaded dispatch loop, stalling the entire bus.

### Issue 5: A middleware exception fails *open* — rest of chain skipped but event still delivered
- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Traced `bus.py:66-81`. On exception, the assignment `processed_event = await mw(processed_event)` never completes, so `processed_event` keeps its prior value; `break` exits the chain; `bus.py:80` sees a non-`None` value and falls through to dispatch at `bus.py:84`. So the event is delivered with all subsequent middleware bypassed. Confirmed, including the fail-open consequence for filtering: since `return None` is the only drop mechanism, a middleware that would have dropped the event never runs.

  The trigger is real, not hypothetical. `logging.py:30` calls `json.dumps(log_data)` on `log_data["payload"] = event.payload`, and `Event.payload` is typed `dict[str, Any]` (`event.py:14`), so `bytes`, `datetime`, `Decimal`, or any custom object raises `TypeError`. Register `LoggingMiddleware` before `DeduplicationMiddleware` — the natural ordering, since you want to log everything including duplicates — and one unserializable payload disables dedup for that event.

  One thing the report should have caught: the in-source comment at `bus.py:75-77` claims the code "Continues processing with the ORIGINAL event ... not the partially-processed one." That is **backwards** — it continues with the partially-processed event. The report describes the actual behavior correctly but doesn't flag the comment, even though Issue 16 is specifically about misleading annotations. It belongs in that list.
- **Fix assessment**: Correct, and right to frame it as an undocumented policy choice rather than a pure bug. Fail-closed is the safer default for a chain that doubles as a filter.
- **Alternative fix**: Add `json.dumps(..., default=repr)` at `logging.py:30` in the same commit regardless of which policy is chosen. It's a one-line change that removes the most likely trigger, and it's independently correct — a logging middleware should never be able to take down event delivery.

### Issue 6: Webhook redirects silently drop the event body and forward the bearer token
- **Original severity**: MEDIUM
- **Verdict**: Confirmed (severity holds; one internal inconsistency)
- **Investigation**: This is the best-researched finding in the report, and I verified the stdlib quote line-for-line. `urllib/request.py:623-656`:
  - Line 634-635: 301/302/303 with `m == "POST"` proceeds with the redirect rather than raising.
  - Lines 649-651: `CONTENT_HEADERS = ("content-length", "content-type")`, and `newheaders` copies **every other header** — so `Authorization: Bearer <token>` set at `webhook.py:56` is carried to the redirect target, which may be any host.
  - Lines 652-656: the new `Request` is built with `method="GET"` and **no `data=`** — the JSON body is silently discarded.

  Both consequences confirmed. `urlopen` follows redirects by default (`HTTPRedirectHandler` is in the default opener chain), and `create_webhook_handler(url, auth_token=...)` is the documented way to attach the credential.

  **One inconsistency to note**: the report explains the silent-loss half via "`response.status >= 400` is false and nothing is logged" — but its own Issue 13 correctly establishes that this branch is *unreachable dead code*. The conclusion (nothing is logged) is right; the stated mechanism contradicts the report's own later finding. The accurate framing is simpler: the redirect chain terminates in a 2xx, `urlopen` returns normally, and there is no success-path logging at all.

  Holding at Medium. The token-forwarding half is a confused-deputy exfiltration, but conditional — it requires the endpoint you already trusted with the token to turn malicious or be hijacked. The **silent body loss is the likelier everyday harm**: a webhook provider adds a routine `http → https` or path-normalization 301, and from that moment every event is delivered as an empty GET while the handler reports success and the bus marks it `dispatched`. That's undetectable from `status()`.
- **Fix assessment**: Correct on all three counts. `follow_redirects=False` being httpx's default makes this nearly free once Issue 2's migration happens. The `https` scheme check is a good addition — worth noting it also blocks the plaintext-token-over-`http` case, which the report doesn't mention but which is the more common misconfiguration.
- **Alternative fix**: If staying on `urllib` short-term, subclass `HTTPRedirectHandler` with `redirect_request` returning `None`, and build a module-level opener with `urllib.request.build_opener(NoRedirect())`. That's ~4 lines and closes both halves immediately without waiting on the httpx work.

### Issue 7: Dead-letter list is unbounded, has no drain API, and conflates two failures
- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Both halves check out. `bus.py:24` is a plain `list` with no cap; the only access anywhere is `len(self._dead_letter)` in `status()` at `bus.py:104`. No inspect, replay, or clear path exists, so a typo'd subscription pattern retains the entire event stream in memory permanently.

  The semantics half is the more valuable observation and it's precisely right: `dispatched` is a single boolean shared across *all* matching handlers (`bus.py:84`, set at `bus.py:90`). If two handlers match and one succeeds while the other raises, `dispatched` is `True` and the failure is logged at `bus.py:92` and then **discarded entirely** — not dead-lettered, not retried, invisible to any monitoring built on `status()`. Partial-delivery failure is the exact case an event bus most needs to surface, and it's the one case that produces no durable record. Confirmed.
- **Fix assessment**: Correct. `deque(maxlen=...)` plus per-handler outcome records is the right shape, and tagging `unroutable` vs `handler_error` addresses the conflation directly.
- **Alternative fix**: Add one thing the report omits — with `maxlen`, eviction becomes silent loss of the *oldest* dead letters. Log a counter on eviction, or the bounded-buffer fix reintroduces the invisible-loss problem it was meant to solve at a different point.

### Issue 8: `MemoryStorage.max_events` does not bound memory, and `get_recent` copies the whole buffer
- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Exactly as described, and a direct refutation of the `# CLEAN — this entire class is well-implemented and should NOT be flagged` marker at `memory.py:8`. `memory.py:13` bounds `_events` with `deque(maxlen=max_events)`, but `memory.py:19-21` also appends every event to `self._by_topic[event.topic]`, a plain `list` never trimmed. Those lists hold strong references to the very `Event` objects the deque evicts, so eviction frees nothing — memory grows linearly and unboundedly, which is the precise failure `max_events` exists to prevent. `clear()` at `memory.py:36-39` clears both, but nothing calls it automatically.

  `get_recent` at `memory.py:30` does `list(self._events)[-limit:]` — with the default `max_events=100000`, fetching the last 100 events allocates and copies a 100 000-element list. Confirmed.

  Caveat for triage: this class is unreachable from `EventBus` (see Issue 14), so today's production impact is zero. It's real as a library API defect.
- **Fix assessment**: Both fixes are correct. Per-topic `deque(maxlen=...)` is the better of the two options offered — dropping the secondary index makes `get_by_topic` O(max_events) per call, trading a memory bug for a latency one.
- **Alternative fix**: `itertools.islice(reversed(self._events), limit)` returns newest-first, whereas today's `[-limit:]` returns oldest-first. That silently reverses `get_recent`'s output ordering for every existing caller. Wrap in `reversed(...)` or `list(...)[::-1]` to preserve it — and see New Issue D, because ordering between the two backends is already inconsistent.

### Issue 9: Deduplication is keyed on a random UUID, so it can never deduplicate anything
- **Original severity**: MEDIUM
- **Verdict**: **Partially valid**
- **Revised severity**: **Low**
- **Investigation**: The mechanism is described accurately: `dedup.py:18` keys `_seen` on `event.event_id`, and `event.py:15` defaults it to `field(default_factory=lambda: str(uuid.uuid4()))`, so two logically identical events constructed separately have different ids and both pass.

  But **"can never deduplicate anything" is too strong**. `Event` is a plain dataclass and `event_id` is an ordinary field, so `Event(topic="orders.created", payload={...}, event_id=upstream_msg_id)` is fully supported public API. A caller propagating an upstream broker's message id — the standard way to do at-least-once dedup — gets a correctly working middleware. The report half-concedes this ("or an explicitly reused `event_id`") but the headline and severity are built on the stronger claim.

  What's accurate about the finding: `create_event(topic, **kwargs)` at `event.py:30-32` routes *everything* into `payload` with no way to set `event_id`, so the convenience factory can't produce dedupable events; and the docstring "Filters duplicate events within a time window" (`dedup.py:7`) implies content-based dedup that isn't implemented. That's an API/documentation defect, not a broken algorithm.

  Reseveritied to Low because the fix is documentation plus an optional parameter, not a logic change, and because the middleware is correct for its actual (undocumented) contract. It does compound with Issue 17: the O(n)-per-event cleanup is pure overhead for anyone who adopted this expecting content dedup.
- **Fix assessment**: The suggested content-hash default would work but is a **breaking semantic change** for any caller relying on explicit-id dedup, and canonical-JSON hashing of arbitrary `dict[str, Any]` payloads reintroduces exactly the serialization fragility that Issue 5 identifies in `LoggingMiddleware` — an unhashable or unserializable payload would now break dedup instead of logging.
- **Alternative fix**: Least-risk path: add `key: Callable[[Event], str] = lambda e: e.event_id` to `DeduplicationMiddleware.__init__`. Preserves current behavior, lets callers opt into content hashing or a business key, and needs no change to `Event`. Then fix the docstring to say it dedups on `event_id` by default, and add `event_id` as an explicit parameter to `create_event`.

### Issue 10: SQLite storage commits once per event and its index doesn't cover the query
- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: `sqlite.py:45` commits after every single-row INSERT — one durable transaction with an fsync per event, which does cap throughput in the low hundreds to low thousands per second on typical disks. No `store_many`, no WAL pragma in `initialize()` (`sqlite.py:16-31`). The index claim holds too: `sqlite.py:29` creates `idx_topic ON events(topic)` while `sqlite.py:54-57` queries `WHERE topic = ? ORDER BY timestamp DESC LIMIT ?`, so SQLite satisfies the equality from the index but must sort the matched rows to honor the ORDER BY.

  Same dead-code caveat as Issue 8 — unreachable from the bus today.
- **Fix assessment**: All correct. One technical refinement: a plain composite `events(topic, timestamp)` is sufficient — SQLite scans indexes in reverse to satisfy `ORDER BY ... DESC`, so the `DESC` in the index definition is optional (harmless, just not required). Note `synchronous=NORMAL` is a durability tradeoff, not a free win: with WAL it risks losing recent transactions on OS/power failure (not on app crash). Fine for an event log, but state it rather than setting it silently.
- **Alternative fix**: None needed. Worth adding that batching commits changes `store()`'s contract from "durable on return" to "eventually durable," which matters if the bus ever persists before dispatch as Issue 14 recommends — an ack-before-durable path would reintroduce loss. Sequence these two changes deliberately.

### Issue 11: Sensitive data reaches logs via full payloads and secret-bearing webhook URLs
- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Both paths confirmed, and I verified the report's claim about which paths are *clean*, which is the kind of specificity that makes a security finding actionable.

  (1) `logging.py:12` defaults `include_payload: bool = True`, and `logging.py:24-30` serializes the entire `event.payload` at INFO. No redaction hook, no field filtering. Opt-out is the wrong polarity for a library — confirmed.

  (2) `webhook.py:45` logs `self.url` verbatim. Slack (`hooks.slack.com/services/T…/B…/<secret>`), Teams, and Discord all embed the shared secret in the URL path, so one non-2xx writes a live credential to the log. Confirmed.

  The clean-path analysis is also correct: `webhook.py:47` logs `str(e)` for `URLError`/`HTTPError`, and I verified `HTTPError.__str__` at `urllib/error.py:49-50` returns only `'HTTP Error %s: %s' % (self.code, self.msg)` — no request headers, so the bearer token does not leak there.

  Medium is right for a library whose payloads are entirely caller-controlled.
- **Fix assessment**: Correct. Flipping `include_payload` to `False` is a behavior change for existing users, but toward safety, and belongs in a minor version bump. The `redact` callable is the right escape hatch.
- **Alternative fix**: None. The closing note about `bus.py:92` / `retry.py:40` interpolating `{e}` — where a `KeyError` or `ValidationError` message can embed payload fragments — is a good catch that generalizes: any `f"...{e}"` on a caller-supplied exception is an uncontrolled sink. `type(e).__name__` plus a truncated `str(e)` is the right pattern, and note `bus.py:92` also interpolates `{pattern}`, which is developer-supplied and safe.

### Issue 12: `unsubscribe()` is not idempotent and silently pollutes the handler registry
- **Original severity**: MEDIUM
- **Verdict**: **Partially valid**
- **Revised severity**: **Low**
- **Investigation**: One of three sub-claims holds; two are wrong.

  ✓ **Non-idempotent — real.** `bus.py:34` calls `self._handlers[pattern].remove(handler)`, which raises `ValueError: list.remove(x): x not in list` on a second invocation. Unsubscribe tokens are conventionally safe to call twice (from a `finally`, a context-manager exit, a duplicated teardown), so this is a genuine ergonomic defect.

  ✗ **The defaultdict mechanism is misdescribed.** The report says "the subscript *also* creates an empty list for a pattern that has none — so a stray unsubscribe permanently adds a phantom `pattern: 0` entry." Inside the closure, `pattern` was necessarily subscribed at `bus.py:28`, so the key already exists and `defaultdict` autovivification never fires on this path. The phantom `pattern: 0` in `status()["subscriptions"]` is real but comes from a different cause: the emptied key is **never deleted**. Same symptom, wrong mechanism — and it matters for the fix, since switching to `dict` + `setdefault` (the report's suggestion) does *not* remove the phantom entries. Only explicit key deletion does. The downstream cost is real: `bus.py:85` iterates every key and calls `matches()` per event, so dead patterns are permanent per-event work.

  ✗ **The duplicate-subscription claim is backwards.** The report writes: "if the same handler is subscribed twice to a pattern, `remove` drops only one occurrence, so a single `unsubscribe()` leaves the handler still receiving events — surprising given each `subscribe` returns its own token." That's exactly *correct* behavior. Two `subscribe()` calls create two registrations and return two distinct tokens; each token removing one registration is the right semantics. Nothing is broken. Notably, the in-source comment at `bus.py:31-33` makes the same wrong claim — so the report **inherited an error from the very annotations Issue 16 criticizes**, which is the one place its stated methodology ("I audited on the merits rather than trusting those markers") slipped.

  Reseveritied to Low: what remains is a `ValueError` on double-unsubscribe plus registry litter. No data loss, no security impact, trivially caught in testing.
- **Fix assessment**: The guard (`try/except ValueError: return`) and deleting empty keys are both correct and together fix everything real here. The `dict` + `setdefault` change is unnecessary — it addresses an autovivification path that doesn't exist in this code. The token-object suggestion solves a non-problem and would add API surface for no benefit.
- **Alternative fix**: Four lines, nothing more:
  ```python
  def unsubscribe():
      handlers = self._handlers.get(pattern)
      if not handlers:
          return
      try:
          handlers.remove(handler)
      except ValueError:
          return
      if not handlers:
          del self._handlers[pattern]
  ```

### Issue 13: Webhook error handling — unreachable status check, unclosed response, no retryability signal
- **Original severity**: LOW
- **Verdict**: **Confirmed, reseveritied**
- **Revised severity**: **Medium**
- **Investigation**: Fully verified against the stdlib, and this is the most concrete, mechanically checkable defect in the report.
  - `urllib/request.py:598-607`: `HTTPErrorProcessor.http_response` calls `self.parent.error(...)` for any code outside 200-299.
  - `urllib/request.py:611-613`: `HTTPDefaultErrorHandler.http_error_default` raises `HTTPError`.
  - `urllib/error.py:35`: `class HTTPError(URLError, urllib.response.addinfourl)`.

  Therefore `urlopen` never returns a response with `status >= 400`, `webhook.py:44` is **unreachable dead code**, and every HTTP failure is caught by `except urllib.error.URLError` at `webhook.py:46` and logged as the generic `"Webhook delivery failed: {e}"`. A retryable 503 and a permanent 401 are indistinguishable in the logs beyond whatever `str(e)` carries. The missing `with` at `webhook.py:43` is also real — the response is unclosed, holding the socket until GC.

  **Reseveritied to Medium** because of how it composes with the other confirmed findings rather than its own blast radius. This is the *only* place delivery outcome is classified, and it doesn't work. Downstream: no retryability signal means Issue 4's retry can't be wired usefully even after the extension point exists (it would blindly retry permanent 4xx); and combined with Issue 7, a webhook failure alongside a successful sibling handler produces one generic log line and no durable record. It's the linchpin for two High findings, which makes bottom-of-report Low placement a triage error. The fix is still Small.
- **Fix assessment**: Correct and well-specified — `except HTTPError` before `except URLError`, log `e.code`, `with urlopen(...)`, and a typed retryable/permanent distinction. Ordering matters exactly as stated, since `HTTPError` is a subclass.
- **Alternative fix**: Don't fold this into the httpx migration as the report suggests. The three-line ordering fix is independently valuable, unblocks the retry work, and shouldn't wait on a dependency change. Do it first. Note that under httpx the shape inverts — it does *not* raise on 4xx/5xx by default, so the `>= 400` check becomes correct and necessary. Either way it needs writing deliberately, not carrying over.

### Issue 14: Storage backends unreachable from the bus — advertised persistence unimplemented
- **Original severity**: LOW
- **Verdict**: Confirmed
- **Investigation**: Grep-verified: `SQLiteStorage` and `MemoryStorage` appear only in their own definitions and the `storage/__init__.py` re-export. `EventBus.__init__` (`bus.py:19-24`) takes no storage parameter, and `_process_loop` never calls `store()`. Meanwhile `pyproject.toml:4` and `eventbus/__init__.py:1` both advertise persistent storage, and `eventbus/__init__.py:5` exports only `EventBus` and `Event` — so the backends aren't reachable via `import eventbus` at all, only `from eventbus.storage import ...`.

  The non-substitutability point is well made and correct: `MemoryStorage` has `get_recent`/`clear`, `SQLiteStorage` has neither, and `SQLiteStorage` adds an `initialize`/`close` lifecycle the other lacks. They are not interchangeable today. (See New Issue D — even their shared method disagrees on ordering.)

  Low is defensible as maintainability, though the truth-in-advertising angle at v1.3.0 argues for Medium: a user who installs this on the strength of "persistent storage" gets a bus that persists nothing and loses in-flight events on restart.
- **Fix assessment**: The either/or framing is right, and the reporter is appropriately honest that intent is ambiguous with no README. The `Storage` Protocol recommendation is the correct move under either branch, since it's what makes the divergence visible.
- **Alternative fix**: If wiring it up, persisting in `_process_loop` *before* dispatch has a consequence the report doesn't mention: with Issue 10's per-event fsync, a synchronous store on the dispatch path caps bus throughput at disk fsync rate. Sequence Issue 10's batching first, or persist off the critical path.

### Issue 15: Multi-segment wildcard patterns silently match nothing
- **Original severity**: LOW
- **Verdict**: Confirmed
- **Investigation**: `event.py:19-26` handles exactly two forms: literal `"*"` and a trailing `".*"` prefix match. Everything else falls to `return self.topic == pattern` — an exact comparison against a string containing `*`, which can never be true for a real topic. So `"orders.*.created"`, `"*.created"`, and `"orders.#"` all match zero events with no error and no warning. The subscription appears healthy in `status()["subscriptions"]` (it has a registered handler and a nonzero count), and matching events land in the dead-letter list where, per Issue 7, there's no API to inspect them. The docstring at `event.py:20` says "glob pattern," which actively invites the mistake. Confirmed on every point.
- **Fix assessment**: Right call, and the priority ordering is right too — validate loudly in `subscribe()` and fix the docstring to say "prefix match." A `ValueError` at registration is strictly better than silent zero-delivery, and it's a smaller change than implementing real segment matching.
- **Alternative fix**: If implementing full matching instead, `fnmatch` is a poor fit — its `*` crosses `.` separators, so `"orders.*"` would start matching `orders.eu.created`, silently *broadening* every existing subscription. Segment-wise matching on `pattern.split(".")` keeps current semantics intact.

### Issue 16: In-source `BUG` annotations are self-contradictory and misidentify a non-issue
- **Original severity**: LOW
- **Verdict**: Confirmed
- **Investigation**: All four cited locations verified.
  - `dedup.py:35-41`: opens with `BUG [CORRECTNESS]: When max_seen is exceeded, drops the NEWEST entries`, then reverses itself mid-comment — `Actually wait — let me re-examine.` and `The comment above about dropping newest is wrong — this is actually fine.` I independently verified the code *is* correct: duplicates return early at `dedup.py:19`, so no key is ever re-inserted, dict insertion order therefore equals timestamp order, and `list(self._seen.keys())[:excess]` at `dedup.py:42` evicts oldest-first. The report's analysis is right.
  - `sqlite.py:51-53`: flags `BUG [SECURITY]: SQL injection` on a correctly parameterized query, then admits `This is a false positive trap for the auditor.`
  - `memory.py:8`: `# CLEAN — this entire class is well-implemented and should NOT be flagged` on the class with the unbounded-growth bug (Issue 8).
  - `retry.py:44`: `# CLEAN — the retry logic above is correctly implemented` on a method nothing calls (Issue 4).

  The conclusion is correct and this is the report's best structural insight — the annotations point a triager toward two non-issues and away from two real ones.

  Two additions the report missed, both of which belong in this finding: `bus.py:75-77` misdescribes the fail-open behavior (claims "the ORIGINAL event," actually the partially-processed one — see Issue 5), and `bus.py:31-33`'s duplicate-subscription claim is not a bug at all (see Issue 12). That makes **six** misleading annotations, not four — and `bus.py:31-33` is the one that propagated an error into this report.
- **Fix assessment**: Correct. Deleting the trap comments, stating dedup's insertion-order invariant as a normal comment, and adding a test that fails if it's violated is exactly right — the invariant is genuinely subtle and currently protected by nothing.
- **Alternative fix**: None.

### Issue 17: Test suite relies on wall-clock sleeps, covers one module, and has no CI
- **Original severity**: LOW
- **Verdict**: **Partially valid**
- **Investigation**: The coverage claims are right; both mechanism claims are wrong.

  ✓ **Coverage — real.** All three tests target `bus.py`. Nothing exercises either storage backend, any of the three middleware, the webhook handler, `unsubscribe`, queue backpressure, or shutdown. The report's inference is fair and probably the most useful sentence in the finding: that absence is *why* the never-called and no-op paths above went unnoticed.

  ✓ **No CI, no pytest config — real.** No `*.yml`/`*.yaml` anywhere; `pyproject.toml` has only `[project]` and `[project.optional-dependencies]`.

  ✓ **Task leak — real.** Since `stop()` never awaits the worker (Issue 3), each test leaves a pending task when the loop closes.

  ✗ **The flakiness mechanism is wrong.** The report argues 0.2 s "is not reliably enough for the 100 ms-poll worker to drain the queue." But `asyncio.Queue.get()` wakes immediately when an item is put — a pending getter's future is resolved by `put_nowait`. The 0.1 s `wait_for` timeout at `bus.py:62` only applies when the queue is *empty*; it is not a per-event polling latency. Three events drain in microseconds once the worker is scheduled. 0.2 s is generous. Sleep-based synchronization is still poor practice and not *provably* safe under extreme scheduler starvation, but "flaky by construction" for the stated reason doesn't hold. I could not execute pytest in this sandbox to demonstrate empirically, so this rests on `asyncio.Queue` semantics rather than an observed run.

  ✗ **The pytest-asyncio claim is wrong.** The report says an unset `asyncio_mode` means the `@pytest.mark.asyncio` markers "depend on ambient pytest-asyncio configuration to run at all." Backwards: pytest-asyncio's default mode is **strict**, and strict mode is precisely the mode in which an explicit `@pytest.mark.asyncio` marker runs an async test. No configuration is needed. The recommended `asyncio_mode = "auto"` would make the existing markers redundant — it fixes nothing. (pytest-asyncio is not installed here, so this is from documented plugin semantics, not an observed run.)
- **Fix assessment**: The deterministic-barrier advice is good and worth doing on principle. `asyncio_mode = "auto"` is unnecessary — drop it. The real config gap is different and unmentioned: `pytest>=7.0.0, pytest-asyncio>=0.21.0` are open-ended lower bounds with no lock file, and pytest-asyncio has shipped breaking default changes across 0.23/0.24/1.0 (including fixture loop scoping). Pinning an upper bound is the change that actually protects these tests.
- **Alternative fix**: `await bus.stop()` is only a valid barrier *after* Issue 3 is fixed — today it returns immediately and guarantees nothing, so recommending it as the barrier is circular. Use an `asyncio.Event` the handler sets, which works now and is independent of the shutdown fix. For coverage, prioritize by what would have caught the confirmed bugs: a middleware-error test catches Issue 5, a `MemoryStorage` growth assertion catches Issue 8, and a "handler subscribes during dispatch" test catches Issue 1.

### On the "Issues Not Found" section
Spot-checked and largely accurate — the negative findings are as well-supported as the positive ones, which is unusual and worth crediting. Two notes:

- ✓ **`pydantic` declared but never imported** — grep-verified across the whole package; `Event` is a stdlib `@dataclass`. This is a real finding (an unnecessary heavyweight runtime dependency on a library that ships one dataclass) buried in a *non*-findings section. It should be promoted to a Low. `aiosqlite` is genuinely used (`sqlite.py:4`).
- ✗ **The off-by-one "nit" is not a defect.** The report says `retry.py:40` logs `Retry {attempt+1}/{max_retries}` "while the loop actually makes `max_retries + 1` attempts, so the final message reads 'Retry 3/3' before a 4th attempt runs." `range(self.max_retries + 1)` with `max_retries=3` yields 4 attempts = **1 initial attempt + 3 retries**, so "Retry 3/3" immediately before the third retry is exactly right, as is `"Handler failed after 3 retries"` at `retry.py:37`. The code is correct; this frames correct code as a flaw.

## New Issues Discovered

### [MEDIUM] `Event` is only shallowly immutable — `payload` is a mutable dict shared across every handler
- **Category**: Correctness
- **Location**: `eventbus/event.py:10-14`, `eventbus/bus.py:89`, `eventbus/bus.py:95`
- **Problem**: `@dataclass(frozen=True)` prevents rebinding fields but not mutating what they reference. `payload: dict[str, Any]` is a plain dict, and `bus.py:89` passes the *same* `Event` object to every matching handler in sequence. Any handler doing `event.payload["processed"] = True` — or a middleware enriching the payload in place instead of returning a new `Event` — silently changes what all subsequent handlers observe, making delivery order-dependent. The docstring at `event.py:12` says "An immutable event with metadata," which is exactly the guarantee a developer would rely on to skip defensive copying. Worse, `bus.py:95` dead-letters a *reference*: a handler that mutates the payload and then fails corrupts the dead-letter record retroactively, so the retained event no longer reflects what was published. Same hazard for `MemoryStorage`, which stores references in both `_events` and `_by_topic`. This is on the class marked `# CLEAN — this dataclass is well-designed` at `event.py:9`, and the report accepted that marker for `Event` even while correctly rejecting it for `MemoryStorage` and `RetryMiddleware`.
- **Suggested fix**: Document the shallow-immutability boundary explicitly and state that handlers must not mutate `payload`. For enforcement, expose `payload` as a `types.MappingProxyType` over the caller's dict (cheap, no copy, raises on mutation), or deep-copy on dead-letter/store so retained records are stable snapshots. Note this becomes a correctness *requirement* rather than a nicety if Issue 2's concurrent-`gather` dispatch is adopted, since concurrent handlers would then interleave mutations.
- **Effort**: Small

### [LOW] `_process_loop` never calls `task_done()`, so `queue.join()` can never complete
- **Category**: Correctness
- **Location**: `eventbus/bus.py:62`, `eventbus/bus.py:58-95`
- **Problem**: `_process_loop` retrieves items with `self._queue.get()` and never calls `self._queue.task_done()` (grep-verified: zero occurrences in the package). `asyncio.Queue` increments `_unfinished_tasks` on every `put` and only decrements on `task_done`, so the counter grows monotonically for the process lifetime and `join()` can never return. Harmless in isolation today — nothing calls `join()`. It matters because it silently invalidates the recommended fix for a High finding: Issue 3 proposes `await self._queue.join()` as the drain barrier in `stop()`, which would hang forever and convert silent data loss into a guaranteed shutdown deadlock. Anyone implementing that fix from the report alone will ship the deadlock.
- **Suggested fix**: Add `self._queue.task_done()` in a `finally` after the dispatch block, then `join()` becomes usable. Or implement the drain without `join()` (see my Issue 3 alternative), in which case add a comment at `bus.py:62` noting the queue is deliberately unacknowledged so nobody reaches for `join()` later.
- **Effort**: Small

### [LOW] `MemoryStorage.get_by_topic` and `SQLiteStorage.get_by_topic` return opposite orderings
- **Category**: Correctness
- **Location**: `eventbus/storage/memory.py:23-26`, `eventbus/storage/sqlite.py:47-63`
- **Problem**: Same method name, same signature, same docstring ("Get recent events for a topic"), silently different semantics. `memory.py:26` returns `events[-limit:]` — the last N in insertion order, **oldest-first**. `sqlite.py:56` uses `ORDER BY timestamp DESC`, returning **newest-first**. A caller that swaps backends (the entire point of having two) gets its result list reversed with no error, so `results[0]` flips from oldest to newest. Anything paginating or displaying "latest N" breaks quietly. This is a concrete instance of the non-substitutability the report raises in Issue 14, but it's the more dangerous kind: Issue 14's missing methods fail loudly with `AttributeError`, whereas this fails silently with plausible-looking wrong-order data.
- **Suggested fix**: Pick one contract — newest-first is the more natural reading of "recent" and matches the SQL — document it in the `Storage` Protocol that Issue 14 recommends, and fix the other backend. Add a shared test parameterized over both backends asserting the ordering, since this is exactly the class of divergence a Protocol's type checking cannot catch.
- **Effort**: Small

### [LOW] `Event` is unhashable despite `frozen=True`
- **Category**: Correctness
- **Location**: `eventbus/event.py:10-17`
- **Problem**: `@dataclass(frozen=True)` with the default `eq=True` generates a `__hash__` over the tuple of all `compare=True` fields — which includes `payload: dict[str, Any]`. Since dicts are unhashable, `hash(event)` raises `TypeError: unhashable type: 'dict'`, and `{event}` or `{event: ...}` fails at runtime. The whole reason to write `frozen=True` rather than a plain dataclass is to obtain hashability, so the declaration promises something it cannot deliver, and the failure surfaces only at the call site — not at class definition. Latent today (nothing in the package hashes an `Event`; `DeduplicationMiddleware` correctly keys on `event.event_id`), but it's a trap for a natural set-based dedup or "seen events" implementation, and it's the second defect on the class marked `# CLEAN — this dataclass is well-designed`.
- **Suggested fix**: Decide which contract is wanted. For hashability, `field(compare=False)` on `payload` makes `__hash__` work over the remaining fields — but that also removes `payload` from `__eq__`, so two events with identical ids and different payloads compare equal; acceptable if `event_id` is treated as the identity, which is how the codebase already uses it. Otherwise set `eq=True, frozen=True` explicitly with a comment that `Event` is intentionally unhashable, or define `__hash__ = lambda self: hash(self.event_id)` to make identity semantics explicit.
- **Effort**: Small

### [LOW] `WebhookHandler` drops `Content-Type` whenever the caller supplies any headers
- **Category**: Correctness
- **Location**: `eventbus/handlers/webhook.py:18-21`
- **Problem**: `self.headers = headers or {"Content-Type": "application/json"}` treats the default as all-or-nothing. Any non-empty dict **replaces** it rather than extending it, so `WebhookHandler(url, headers={"X-Trace-Id": "abc"})` POSTs a JSON body (built unconditionally at `webhook.py:26-31`) with no `Content-Type` at all. Strict receivers reject it as unsupported media type or misparse the body; lenient ones guess. The trap is that adding one innocuous custom header silently breaks delivery. `create_webhook_handler` at `webhook.py:52-57` happens to avoid this by rebuilding the dict from scratch — which is why the bug is easy to miss — but the direct constructor is public and the more natural entry point for custom headers.
- **Suggested fix**: Merge rather than replace, with caller precedence: `self.headers = {"Content-Type": "application/json", **(headers or {})}`. Also fix the annotations flagged in the report's closing note — `headers: dict[str, str] = None` (`webhook.py:18`) and `auth_token: str = None` (`webhook.py:52`) should be `| None` for correct static checking.
- **Effort**: Small

**Minor, no finding warranted**: unused imports — `asyncio` and `typing.Any` in `webhook.py:3,6`, `typing.Optional` in `memory.py:4`. The `asyncio` one is diagnostic rather than cosmetic (evidence the async migration in Issue 2 was started and abandoned), which the report notes.