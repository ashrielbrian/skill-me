# PR Independent Review

## PR
acme/parcelpost#3 — Bulk shipment creation and uniform carrier retries
https://github.com/acme/parcelpost/pull/3
7 files changed, +153/-7 lines, head f93e98f

## What this PR does (from the diff)
The PR adds `POST /shipments/bulk`. The handler parses a list of items. A new service function then gets a rate and buys a label for each item inside one SQLite transaction, and inserts one row per item. The PR also adds a retry loop with linear backoff inside `CarrierClient._post`. Two new settings control the loop. Every carrier call now retries on `urllib.error.URLError`, and this includes `create_label`.

## What the author says it does
The author says that each bulk item takes the same fields and the same validation as `POST /shipments`. The author also says that the batch is all-or-nothing, so "the customer is not left with half a batch". The author says that retries now live in `CarrierClient._post`, so every carrier call gets them, and that there is "No change to single shipment creation."

## The underlying problem
There are two problems. First, customers need to create many shipments in one request, with no partial result. Second, the author says that some carrier calls do not survive a transient network error. But at base c386fca, every `get_rate` call already uses `with_retry` (`parcelpost/services/quotes.py:8`, `parcelpost/services/shipments.py:19`). The only call without it, `create_label`, has no retry on purpose (`parcelpost/services/shipments.py:20-21`).

My minimal sketch for the bulk part changes four existing places: the handler, the service, the repo, and the client (to add `void_label`). It reuses the single-shipment parse steps and the "carrier calls first, short transaction after" pattern. For the retry part, the minimal sketch is no change. The bulk part is close to that size, but it does not void bought labels and it does not write events. The retry part is extra machinery, and its only new effect is to retry `create_label`.

## Summary
Do not merge the PR in its current state. It does not do what the description claims, and it adds two ways to lose money. The most important finding: the new loop in `_post` retries `create_label`, which the code marks as not idempotent. One gateway error can then buy two labels on `POST /shipments`, and the description says that this endpoint does not change. Also, the bulk endpoint keeps paid labels when an item fails, and it does not write the 'labelled' event.

## Findings

### [CRITICAL] `_post` now retries `create_label`, so one purchase can buy two or three labels
- **Category**: Correctness
- **Location**: parcelpost/clients/carrier.py:25-32 (head f93e98f)
- **Problem**: The new loop retries every `urllib.error.URLError`. `create_label` (`parcelpost/clients/carrier.py:41-43`) goes through `_post`, so a label purchase now gets 3 attempts by default (`CARRIER_RETRIES=3`). `HTTPError` is a subclass of `URLError`. Thus a 502, 503, or 504 from a gateway in front of the carrier causes a second `POST /labels`. If the carrier completed the first purchase, the customer pays for two labels and the DB records one. This affects the current `POST /shipments` and also the new bulk endpoint. The loop also retries 4xx responses, which cannot succeed on a second try.
- **Evidence**:
  - At the head, `parcelpost/services/shipments.py:20-21` still says: "# Not retried: create_label is not idempotent at the carrier, and a retry / # after a timeout can buy a second label." The PR makes this comment false.
  - I ran the client against a fake `urlopen` that returned `HTTPError(504, "Gateway Timeout")` and then `{"label_id": "LBL-2"}`. `create_label` returned `LBL-2` after two calls to `https://carriers.test/labels`.
  - A fake 400 response caused 3 calls and the error `carrier call rates failed after 3 attempts: Bad Request`.
- **Suggested fix**: Remove the retry from `_post`. Keep `create_label` at one attempt. If retries must live in the client, add them to `get_rate` only, and use `with_retry`. Do not retry an `HTTPError` with a 4xx status. Add a test that asserts exactly one `POST /labels` call on a 504.
- **Effort**: Small (< 1 hour)

### [CRITICAL] Bulk rollback deletes the rows but keeps the bought labels
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:40-56 (head f93e98f)
- **Problem**: The service buys each label at the carrier inside `db.transaction()`. When item k fails, the transaction rolls back the rows for items 1 to k-1. The carrier does not roll back, so the labels for items 1 to k-1 stay bought and paid. No row refers to them, and no code voids them. The rollback also deletes the only record of these label ids, and the failure path does not log them. The customer gets half a batch at the carrier and has no record of it.
- **Problem (second effect)**: `BEGIN IMMEDIATE` (`parcelpost/db.py:39`) takes the SQLite write lock at once. The batch holds this lock across all of its carrier calls. In the worst case, one item takes about 66.6 s: 50.1 s for `get_rate` plus 16.5 s for `create_label` (see the next finding).
- **Evidence**:
  - Precedent: `create_shipment` (`parcelpost/services/shipments.py:18-30`) makes the carrier calls first. It opens a short transaction only around the DB writes.
  - `FakeCarrier` already has `void_label` (`tests/conftest.py:19-20`), but `CarrierClient` has no `void_label`.
  - I ran the scenario from `test_bulk_is_all_or_nothing`: 3 items, and the third `get_rate` raises `UpstreamError`. The result was `status 502 labels bought 2 voided [] rows []`.
- **Suggested fix**:
  1. Get the rates for all items first.
  2. Buy the labels one at a time, outside the transaction. Keep each label id.
  3. If a later step fails, void each bought label. Log each label id that the void does not release.
  4. Open one short transaction for the inserts and the events.
  5. Add `void_label` to `CarrierClient`.
- **Effort**: Medium (hours)

### [HIGH] The PR adds a second retry policy and puts it under `with_retry`
- **Category**: Architectural fit
- **Location**: parcelpost/clients/carrier.py:24-35, parcelpost/services/shipments.py:42-44 (head f93e98f)
- **Problem**: `README.md:15` calls `with_retry` "the one retry policy for carrier calls." The PR adds a second policy in `_post`, with linear backoff and no jitter. The PR keeps `with_retry` at each `get_rate` call site, and it adds one more at `parcelpost/services/shipments.py:42-44`. The two policies multiply, so one `get_rate` now makes up to 9 HTTP attempts, not 3. Worst case, with `CARRIER_TIMEOUT_S=5`:
  - `get_rate` before: 3 × 5 s + up to 0.6 s of sleep = about 15.6 s.
  - `get_rate` after: 9 × 5 s + 4.5 s of `_post` sleep + up to 0.6 s = about 50.1 s.
  - `create_label`: from 5 s to 3 × 5 s + 1.5 s = 16.5 s.
- **Problem (continued)**: Linear backoff with no jitter makes concurrent requests retry at the same times. `with_retry` uses full jitter to prevent this (`parcelpost/retry.py:18`, `parcelpost/retry.py:29`). Also, no call site at c386fca "forgot" `with_retry`. So the new loop fixes no current call site, and its only new effect is the first finding.
- **Evidence**: I sent `POST /quotes` while every `urlopen` raised `URLError("connection reset")`. The result was status 502 after 9 HTTP calls.
- **Suggested fix**: Delete the loop in `_post`. Delete `CARRIER_RETRIES` and `CARRIER_BACKOFF_S`. If retries must live in the client, call `with_retry` inside `get_rate` only, and remove the call-site wrappers. Keep one policy.
- **Effort**: Small (< 1 hour)

### [HIGH] Bulk shipments have no 'labelled' event
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:46-56 (head f93e98f)
- **Problem**: The bulk loop inserts rows, but it does not call `shipments_repo.add_event`. The code states an invariant at `parcelpost/services/shipments.py:24-25`: "The row and its first event commit together, so a shipment never exists without its 'labelled' event." Every bulk shipment breaks this invariant. A reader of `shipment_events` sees bulk shipments with no history.
- **Evidence**: I created one bulk item and then one single shipment. The result was `shipment_events == [{'shipment_id': 2, 'kind': 'labelled'}]`. Shipment 1, from the bulk path, has no event.
- **Suggested fix**: In the transaction, call `shipments_repo.add_event(shipment_id, "labelled")` for each row. A better fix: move the insert and the event from `create_shipment` into one helper, and call it from both paths. Add a test for the bulk event.
- **Effort**: Small (< 1 hour)

### [HIGH] The description says "No change to single shipment creation", but the diff changes it
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: The diff does not support these claims in the description:
  - "No change to single shipment creation." `POST /shipments` now gives `create_label` up to 3 attempts and `get_rate` up to 9 attempts. `POST /quotes` also changes.
  - "the customer is not left with half a batch". This is true for DB rows only. The carrier keeps the labels.
  - "Retries now live in `CarrierClient._post`". The call-site `with_retry` wrappers stay, and the PR adds a new one at `parcelpost/services/shipments.py:42-44`.
  - "goes through the same validation". The bulk parse steps differ from the single parse steps (see the `declared_value` finding).
- **Problem (effect)**: A reviewer who trusts the first claim does not check the riskiest change in the PR.
- **Evidence**: The diff, and the stale comment at `parcelpost/services/shipments.py:20-21`.
- **Suggested fix**: After the fixes, correct the description. List each behavior change to a current endpoint.
- **Effort**: Small (< 1 hour)

### [MEDIUM] The bulk handler copies the parse steps of `POST /shipments`, and the copy already differs
- **Category**: Architectural fit
- **Location**: parcelpost/handlers/shipments.py:28-37 (head f93e98f)
- **Problem**: The bulk handler repeats the parse steps of `create_shipment` (`parcelpost/handlers/shipments.py:10-17`) by hand. The copy differs for `declared_value`:
  - When the field is absent, bulk stores `declared_cents = 0` and single stores `NULL`. The column is nullable (`parcelpost/schema.sql:9`). Now "not declared" has two values, and 0 also looks like "declared as 0".
  - When the field is `null`, bulk returns 422 because `to_cents(None)` fails. Single returns 201.
- **Problem (continued)**: The service also copies `create_shipment` (`parcelpost/services/shipments.py:36-56` against `:11-33`). The missing event is the first difference that came from that copy.
- **Evidence**: I created one bulk shipment and one single shipment, both without `declared_value`. The rows were `[{'id': 1, 'declared_cents': 0}, {'id': 2, 'declared_cents': None}]`. With `"declared_value": None`, bulk returned 422 and single returned 201.
- **Suggested fix**: Make one parse function, for example `_parse_shipment(fields: dict) -> dict`, and call it from both handlers. In the service, share the carrier steps and the write step the same way.
- **Effort**: Small (< 1 hour)

### [MEDIUM] `CARRIER_RETRIES` counts attempts, and `0` breaks every carrier call
- **Category**: API & interface design
- **Location**: parcelpost/clients/carrier.py:11, parcelpost/clients/carrier.py:25, parcelpost/clients/carrier.py:33-35, parcelpost/config.py:10 (head f93e98f)
- **Problem**: `retries=3` gives 3 attempts, which is 2 retries. The error text says "after 3 attempts". The constructor default `retries=1` means no retry, but the settings default is 3. An operator who sets `CARRIER_RETRIES=0` to stop retries gets no HTTP call at all. `last` stays `None`, so `last.reason` raises `AttributeError`. The router then returns 500 "internal error" for every quote and every shipment (`parcelpost/http.py:54-58`). The sibling API uses the name `attempts` (`parcelpost/retry.py:13`).
- **Evidence**: `CarrierClient(..., retries=0).get_rate(...)` raised `AttributeError 'NoneType' object has no attribute 'reason'` after 0 HTTP calls.
- **Suggested fix**: If the fix for the retry-policy finding keeps a setting, name it `CARRIER_ATTEMPTS`. Reject a value below 1 at startup. Use one default value.
- **Effort**: Small (< 1 hour)

### [MEDIUM] The bulk endpoint has no limit on the number of items
- **Category**: Performance
- **Location**: parcelpost/handlers/shipments.py:28, parcelpost/services/shipments.py:41-45 (head f93e98f)
- **Problem**: The endpoint accepts any number of items. Each item makes at least 2 sequential carrier calls, and each label is a paid purchase. In the current design, the batch holds the SQLite write lock for all of these calls. One request with 1,000 items makes at least 2,000 sequential carrier calls before it responds. This is new attack surface, because one request can buy any number of labels.
- **Evidence**: `for item in body["items"]:` has no length check.
- **Suggested fix**: Add a maximum batch size, chosen by product (for example 50). Return 422 above the maximum. Document the limit in the README row.
- **Effort**: Small (< 1 hour)

### [MEDIUM] The all-or-nothing test checks only the DB
- **Category**: Testing
- **Location**: tests/test_bulk.py:29-41, tests/test_carrier_client.py (head f93e98f)
- **Problem**: `test_bulk_is_all_or_nothing` passes while the code leaks 2 labels. The test asserts only `db.query("SELECT * FROM shipments") == []`. It does not check `fake_carrier.labels` or `fake_carrier.voided`. The tests also have these gaps:
  - No test checks that `create_label` makes exactly one attempt.
  - No test sends an `HTTPError` to the client. The tests send only `URLError`.
  - No test checks the 'labelled' event for bulk rows.
  - No test covers an absent `declared_value` in bulk, or `CARRIER_RETRIES=0`.
  - In `test_bulk_validates_each_item`, validation fails in the handler before any service call. Its DB assertion cannot fail, so the test does not test the transaction.
- **Suggested fix**: In the all-or-nothing test, assert `fake_carrier.voided == ["LBL-1", "LBL-2"]`. Add one test for each gap in the list.
- **Effort**: Small (< 1 hour)

### [LOW] A malformed `items` returns 500, and an empty list returns 201
- **Category**: Error handling
- **Location**: parcelpost/handlers/shipments.py:28-29 (head f93e98f)
- **Problem**: The handler loops over `body["items"]` with no type check, and `require_fields` calls `.get` on each item. Thus `"items": "abc"`, `"items": 5`, and `"items": [1]` raise `AttributeError` or `TypeError`. The client gets 500 "internal error" and not 422. `"items": []` returns 201 with `{"items": []}` and creates nothing. Also, a validation error does not name the item that failed, so a client cannot find the bad item in a large batch.
- **Evidence**: The results were `'abc' 500`, `5 500`, `[1] 500`, and `[] 201 {'items': []}`.
- **Suggested fix**: Raise `ValidationError` for an `items` that is not a list, for an empty list, and for an item that is not an object. Put the item index in the message, for example `items[3].weight_g must be positive`.
- **Effort**: Small (< 1 hour)

### [LOW] The service calls `db.query` directly
- **Category**: Architectural fit
- **Location**: parcelpost/services/shipments.py:59-62 (head f93e98f)
- **Problem**: `README.md:11` says "Only repos call `parcelpost.db`." The bulk service builds an `IN (...)` SQL string and calls `db.query`. All other reads in this service go through the repo (`parcelpost/services/shipments.py:33`, `:37`, `:41`).
- **Evidence**: The SQL lives in the repo for the sibling reads: `parcelpost/repos/shipments.py:5-16`.
- **Suggested fix**: Add `shipments_repo.get_many(ids)` and call it. Keep `db.transaction()` in the service, because `README.md:10` says "Services own transactions".
- **Effort**: Small (< 1 hour)

### [LOW] The PR bundles two unrelated changes
- **Category**: Scope & PR hygiene
- **Location**: PR-wide
- **Problem**: The retry change affects every carrier call on production paths. The bulk endpoint is a new feature. The two changes do not depend on each other. The bulk title of the PR hides the riskier change.
- **Suggested fix**: Move the retry change to its own PR, or drop it (see the retry-policy finding).
- **Effort**: Small (< 1 hour)

## Issues Not Found
I found no issues in Security (other than the item-limit finding), Best practices & idioms, Observability, or Maintainability. The stale comment and the missing label-id log are in the first two findings. The new log lines do not write sensitive data. I ran the 24 tests at the head and all pass. I ran my probes in a temporary worktree, and I removed that worktree after the probes.

## Open Questions
1. **Server concurrency.** `parcelpost/db.py:13` shares one connection across threads (`check_same_thread=False`). If the server runs requests on many threads, a concurrent `BEGIN IMMEDIATE` during a long bulk transaction raises `OperationalError`. Also, reads on that connection see uncommitted bulk rows. To answer this, tell me how the service runs: threads, processes, or one worker.
2. **Idempotency key for labels.** Does the carrier `POST /labels` API accept an idempotency key? If it does, a safe retry of `create_label` is possible. To answer this, check the carrier API documentation.
3. **Void endpoint.** `FakeCarrier.void_label` (`tests/conftest.py:19-20`) suggests that the carrier can void a label. To answer this, confirm the real endpoint and whether a void gives a full refund.
4. **Batch size.** Which maximum batch size does product need? The answer sets the limit in the item-limit finding.