# PR Independent Review

## PR
acme/parcelpost#3 — Bulk shipment creation and uniform carrier retries
https://github.com/acme/parcelpost/pull/3
7 files changed, +153/-7 lines, head f93e98f

## What this PR does (from the diff)
The PR adds a `POST /shipments/bulk` route. For each item, the new service function gets a rate, buys a label, and inserts a row. All of this occurs inside one database transaction. The PR also adds a retry loop to `CarrierClient._post`, with two new environment settings for the attempt count and a linear backoff. It also adds tests for both parts.

## What the author says it does
The author says that the bulk endpoint creates many shipments for one customer, and that each item uses the same fields and the same validation as `POST /shipments`. The author says that the batch is all-or-nothing, so "the customer is not left with half a batch". The author also says that retries "now live in `CarrierClient._post`, so every carrier call gets them", and that there is "No change to single shipment creation".

## The underlying problem
Customers must send one request for each shipment, and they need one request that creates a batch. The second problem in the description is that a carrier call gets no retry if its call site does not use `with_retry`. But every `get_rate` call site at the base already uses `with_retry` (`parcelpost/services/quotes.py:8`, `parcelpost/services/shipments.py:19`). The only call without a retry is `create_label`, and the code does that on purpose (`parcelpost/services/shipments.py:20-21`).

My minimal sketch has these parts:
- One item parser, taken out of the `create_shipment` handler.
- All carrier calls done before the transaction.
- One transaction that inserts the rows and the `labelled` events with the current repo functions.
- A void of the bought labels if a later item fails.
- No change to the retries.

The bulk part of the diff is about the size of this sketch, but it copies the single-create logic and does not reuse it. The retry part is outside the sketch: no call site needs it, and it removes a deliberate safety rule.

## Summary
The bulk endpoint does its main job, but the retry change is not safe. `_post` now retries every carrier call, including `create_label`, which the code calls "not idempotent at the carrier". An HTTP 5xx after a purchase can now buy a second label. The bulk path also has three defects:
- A failed batch keeps the labels that it bought at the carrier.
- Bulk rows have no `labelled` event.
- Bulk items parse `declared_value` in a different way from the single path.

The most important finding is the `create_label` retry. It also changes single shipment creation, but the description says that single shipment creation does not change.

## Findings

### [Critical] `_post` now retries `create_label`, so one request can buy two labels
- **Category**: Correctness
- **Location**: parcelpost/clients/carrier.py:24-35 (head f93e98f)
- **Problem**: The retry loop is in `_post`, and `_post` serves both `get_rate` and `create_label`. The code does not retry `create_label` on purpose. The comment at `parcelpost/services/shipments.py:20-21` says that a retry "can buy a second label". After this PR, `create_label` gets up to 3 attempts with the default `CARRIER_RETRIES=3`. `urllib.error.HTTPError` is a subclass of `URLError`, so an HTTP 500, 502, 503, or 504 starts a retry. If the carrier bought the label before it sent the 5xx, the retry buys a second label. Nothing stores the first `label_id`, so nobody can void it.

  The problem affects both single and bulk creation. The comment at `parcelpost/services/shipments.py:20-21` is now false. A read timeout in `getresponse()` raises `TimeoutError`, not `URLError`, so a read timeout does not start a retry. The 5xx case does.
- **Evidence**:
  ```python
  except urllib.error.URLError as exc:
      last = exc
      if attempt < self.retries - 1:
          time.sleep(self.backoff_s * (attempt + 1))
  ```
  I ran a probe script against the PR head. `CarrierClient(..., retries=3).create_label(...)` with a stub `urlopen` that raises `HTTPError` 500 made 3 HTTP calls and raised `carrier call labels failed after 3 attempts: Internal Server Error`.
- **Suggested fix**: Remove the retry from `_post`. If a client-level retry is necessary, add an `idempotent` flag to `_post`. Pass `idempotent=False` from `create_label`. Retry only when the flag is true. Add a test that shows one HTTP call from `create_label` on a 5xx.
- **Effort**: Small (< 1 hour)

### [High] Two retry layers stack: up to 9 HTTP calls for each `get_rate`, with two different backoff policies
- **Category**: Architectural fit
- **Location**: parcelpost/clients/carrier.py:24-35; parcelpost/services/quotes.py:8; parcelpost/services/shipments.py:19; parcelpost/services/shipments.py:42-44 (head f93e98f)
- **Problem**: The description says that retries "now live in `CarrierClient._post`". But the PR keeps the two `with_retry` call sites, and the new bulk code adds a third. `with_retry` retries on `UpstreamError`, and `_post` raises `UpstreamError` after its last attempt. Each `get_rate` call can therefore make 3 × 3 = 9 HTTP calls.

  The worst case with the defaults (`CARRIER_TIMEOUT_S=5`, 3 attempts, 0.5 s linear backoff) is:
  - One `_post`: 3 × 5 s + 0.5 s + 1.0 s = 16.5 s.
  - One `get_rate` under `with_retry`: 3 × 16.5 s + up to 0.6 s of jitter = 50.1 s.
  - One single shipment: 50.1 s + 16.5 s = 66.6 s. Before the PR, the worst case was 15.6 s + 5 s = 20.6 s.

  A 4xx `HTTPError` is a permanent error, but it also starts the 9 calls. README.md:15 says that `with_retry` is "the one retry policy for carrier calls". `with_retry` uses exponential backoff with full jitter (`parcelpost/retry.py:29`). The new loop uses linear backoff with no jitter (`parcelpost/clients/carrier.py:32`). Without jitter, all clients retry at the same times after a carrier outage.
- **Evidence**: The precedent is `parcelpost/retry.py:10-30` and README.md:15. In my probe, `with_retry(lambda: c.get_rate(...))` made 9 HTTP calls with a stub that always raises `URLError`. It also made 9 calls with a stub that raises `HTTPError` 400.
- **Suggested fix**: Keep one retry layer. The simplest choice is to revert the `_post` loop and the two new settings, and keep `with_retry` at the call sites. If you prefer the retry in the client, call `with_retry` inside `get_rate` only. Then delete the wrappers at the call sites. In both cases, do not retry a 4xx `HTTPError`.
- **Effort**: Small (< 1 hour)

### [High] The bulk path keeps the SQLite write lock during all carrier calls
- **Category**: Architectural fit
- **Location**: parcelpost/services/shipments.py:40-45 (head f93e98f)
- **Problem**: `db.transaction()` starts with `BEGIN IMMEDIATE` (`parcelpost/db.py:39`), which takes the database write lock at once. The bulk loop then makes 2 carrier calls for each item inside the lock. For N items, the lock stays held during all 2N network calls. With the stacked retries, that is up to 66.6 s for each item.

  Writers on other connections wait for the sqlite3 default busy timeout of 5.0 s, then fail with "database is locked". All requests in one process share one connection (`parcelpost/db.py:7-20`). If the server runs requests on more than one thread, a `db.transaction()` in a different request gets "cannot start a transaction within a transaction". `dispatch` changes that error to HTTP 500 (`parcelpost/http.py:54-58`).
- **Evidence**: The precedent in the same file makes the carrier calls first. It opens the transaction only for the insert and the event (`parcelpost/services/shipments.py:18-30`).
- **Suggested fix**: Make all carrier calls before `with db.transaction():`. Keep only `shipments_repo.insert` and `shipments_repo.add_event` inside the transaction.
- **Effort**: Small (< 1 hour)

### [High] A failed batch leaves paid labels at the carrier, with no record
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:40-56 (head f93e98f)
- **Problem**: The loop buys the label for an item before it starts the next item. If item k fails, the rollback deletes the rows for items 1 to k-1, but the carrier keeps those k-1 labels. The company pays for them. The rollback deletes the only record of these `label_id` values. The log line at line 58 does not run on failure, so the log has no record either. Nobody can void or reconcile the labels. The all-or-nothing claim is true only for the database rows.
- **Evidence**: `tests/test_bulk.py:30-41` makes the third item fail. It checks only `db.query("SELECT * FROM shipments") == []`, and it does not check `fake_carrier.labels`. My probe used the same input. It got status 502, zero rows, and 2 labels bought at the fake carrier. The test fake has a `void_label` method (`tests/conftest.py:19-20`), but `CarrierClient` has no `void_label` method.
- **Suggested fix**: Get all rates first, because a rate costs nothing. Then buy the labels. If a purchase fails, void the labels that the batch already bought, and log each `label_id`. Add `void_label` to `CarrierClient`. In the test, assert that `fake_carrier.voided` contains the bought labels. If the carrier has no void API, return a result for each item and remove the all-or-nothing claim.
- **Effort**: Medium (hours)

### [High] Bulk shipments have no `labelled` event
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:46-56 (head f93e98f)
- **Problem**: The single path writes the row and its `labelled` event in one transaction (`parcelpost/services/shipments.py:26-30`). The comment at lines 24-25 says that "a shipment never exists without its 'labelled' event". The bulk path calls `shipments_repo.insert` but never calls `shipments_repo.add_event`. So every bulk shipment breaks this rule. Each bulk call adds more rows without events, and a later fix must backfill them.
- **Evidence**: My probe made one single shipment and one bulk shipment. `shipment_events` contained only `[{'shipment_id': 1, 'kind': 'labelled'}]`. Shipment 2, from the bulk call, had no event. `tests/test_bulk.py` does not check events.
- **Suggested fix**: Call `shipments_repo.add_event(shipment_id, "labelled")` after each insert in the bulk transaction. A better fix is one shared insert helper (see the duplication finding below).
- **Effort**: Small (< 1 hour)

### [High] The description makes claims that the diff does not support
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: These claims do not match the diff:
  - "No change to single shipment creation": this is false. `create_shipment` now makes up to 9 HTTP calls for the rate and up to 3 for the label (see the Critical finding and the first High finding).
  - "Retries now live in `CarrierClient._post`": this is only partly true. The `with_retry` wrappers stay, so there are two retry layers.
  - "A carrier call only survived a transient network error if its call site remembered to wrap it in `with_retry`": no `get_rate` call site lacks the wrapper. The only unwrapped call is `create_label`, and that is by design.
  - "so every carrier call gets them": a read timeout in `getresponse()` raises `TimeoutError`, which `_post` does not catch. So that error gets no retry.
  - "goes through the same validation": this is false. The `declared_value` parse is different (see the Medium finding below).
  - "the customer is not left with half a batch": this is true for the rows. It is false for the labels at the carrier.
- **Evidence**: See the findings above for each point.
- **Suggested fix**: Correct the description after the fixes. State which carrier calls get a retry, and why.
- **Effort**: Small (< 1 hour)

### [Medium] The bulk code copies the single-create logic, and the copy already differs
- **Category**: Architectural fit
- **Location**: parcelpost/handlers/shipments.py:28-37; parcelpost/services/shipments.py:41-56 (head f93e98f)
- **Problem**: The bulk handler loop repeats the field parse from `create_shipment` (`parcelpost/handlers/shipments.py:10-17`). The bulk service loop repeats the rate, label, and insert steps from `create_shipment` (`parcelpost/services/shipments.py:18-30`). The copies already differ in two ways: the `declared_value` default, and the absent `labelled` event. Each future change to shipment creation must now change two places.
- **Evidence**: The siblings are `parcelpost/handlers/shipments.py:10-17` and `parcelpost/services/shipments.py:18-30`.
- **Suggested fix**: Add a `_parse_shipment(body) -> dict` function to the handler module, and call it from both handlers. In the service, add one helper that buys the label and one helper that inserts the row with its event. Call both helpers from `create_shipment` and `create_shipments_bulk`.
- **Effort**: Small (< 1 hour)

### [Medium] One PR holds two unrelated changes
- **Category**: Scope & PR hygiene
- **Location**: PR-wide
- **Problem**: The bulk endpoint and the retry change do not depend on each other. The retry change alters every carrier call, including quotes and single shipment creation. A reviewer who reads this PR as a bulk feature can easily miss that change. A revert of one part also reverts the other part.
- **Evidence**: The PR title names both parts. The changes in `parcelpost/clients/carrier.py` and `parcelpost/config.py` have no connection to the bulk route.
- **Suggested fix**: Split the PR into two PRs. Merge the bulk endpoint with the fixes above. Review the retry change separately, or drop it.
- **Effort**: Small (< 1 hour)

### [Medium] `CARRIER_RETRIES` counts attempts, and `CARRIER_RETRIES=0` makes every carrier call fail
- **Category**: API & interface design
- **Location**: parcelpost/clients/carrier.py:11, parcelpost/clients/carrier.py:25, parcelpost/clients/carrier.py:33-35, parcelpost/config.py:10 (head f93e98f)
- **Problem**: The name says "retries", but the loop uses the value as the total number of attempts. `CARRIER_RETRIES=3` gives 3 attempts, which is 2 retries. An operator who sets `CARRIER_RETRIES=0` to stop retries gets zero attempts. Then `last` stays `None`, and `last.reason` raises `AttributeError`. Every quote and every shipment then fails with HTTP 500, and no carrier call occurs. The constructor default is `retries=1`, but the setting default is `3`, so one setting has two defaults.
- **Evidence**: In my probe, `CarrierClient("https://x", 1, retries=0).get_rate(...)` raised `AttributeError: 'NoneType' object has no attribute 'reason'`. The sibling API calls the same value `attempts` (`parcelpost/retry.py:13`).
- **Suggested fix**: Rename the parameter to `attempts` and the setting to `CARRIER_ATTEMPTS`. Reject a value below 1 at startup. Keep one default. If you remove this loop (see the first High finding), this finding goes away too.
- **Effort**: Small (< 1 hour)

### [Medium] The bulk endpoint has no limit on the number of items
- **Category**: Security
- **Location**: parcelpost/handlers/shipments.py:25-28 (head f93e98f)
- **Problem**: The route is new attack surface. One request can send any number of items. Each item makes at least 2 sequential carrier calls and buys a paid label, all inside the write lock. One large request can keep the write lock for minutes and buy many labels.
- **Evidence**: `require_fields(body, "customer_id", "items")` checks only that `items` is not `None` and not `""`. No code checks the length.
- **Suggested fix**: Add a maximum batch size, for example 100 items. Return 422 above the limit. Put the limit in README.md:23.
- **Effort**: Small (< 1 hour)

### [Medium] Bulk items parse `declared_value` differently from `POST /shipments`
- **Category**: Correctness
- **Location**: parcelpost/handlers/shipments.py:35 (head f93e98f)
- **Problem**: The single handler stores `None` when `declared_value` is absent or null (`parcelpost/handlers/shipments.py:11`, `parcelpost/handlers/shipments.py:17`). The bulk handler uses `item.get("declared_value", 0)`, which has two effects:
  - An item without `declared_value` stores `declared_cents = 0`, not `NULL`. The schema lets the column be `NULL` (`parcelpost/schema.sql:9`). The value `0` means "the declared value is zero", which is different from "no declared value".
  - An item with `"declared_value": null` gets 422 `declared_value is not a valid amount`, but the single endpoint returns 201 for the same field.
- **Evidence**: In my probe, a single shipment without `declared_value` gave `declared_cents` = `None`, and a bulk item gave `0`. `tests/test_bulk.py:13-20` does not check `first["declared_cents"]`.
- **Suggested fix**: Use the same expression as `parcelpost/handlers/shipments.py:17`. The shared parser from the duplication finding fixes this for both handlers.
- **Effort**: Small (< 1 hour)

### [Medium] The tests pass with all the defects above
- **Category**: Testing
- **Location**: tests/test_bulk.py:13-41; tests/test_carrier_client.py:11-38 (head f93e98f)
- **Problem**: The new tests do not detect these defects:
  - `test_bulk_is_all_or_nothing` (`tests/test_bulk.py:30-41`) does not check `fake_carrier.labels` or `fake_carrier.voided`.
  - `test_bulk_creates_every_shipment` (`tests/test_bulk.py:13-20`) does not check `first["declared_cents"]` or the `shipment_events` rows.
  - `tests/test_carrier_client.py` tests only `get_rate`. No test shows that `create_label` does not retry, that a 4xx does not retry, or that `retries=0` fails cleanly.
  - No test calls `with_retry` around a `CarrierClient` that also retries, which is how production runs.
- **Evidence**: pytest was not installed for the available Python 3.15, so I did not run the suite. I ran a probe script against the PR head, and it showed each defect above.
- **Suggested fix**: Add assertions for the label count, the voided labels, `declared_cents is None`, and one event for each bulk row. Add client tests for `create_label` on a 5xx, for a 4xx, and for a value below 1.
- **Effort**: Small (< 1 hour)

### [Low] Malformed `items` gives HTTP 500, and an empty list gives HTTP 201
- **Category**: Correctness
- **Location**: parcelpost/handlers/shipments.py:25-29 (head f93e98f)
- **Problem**: The handler does not check that `items` is a list of objects. If `items` is a string, an object, a number, or a list with a non-object, the handler raises `AttributeError` or `TypeError`. `dispatch` changes that to HTTP 500 `internal` and logs a stack trace (`parcelpost/http.py:54-58`). The correct status is 422. An empty list passes, opens and commits an empty transaction, and returns 201 `{"items": []}`. The error messages for an item also do not say which item failed, so the client cannot find the bad item in a large batch.
- **Evidence**: In my probe, `items` set to `"abc"`, `{"a": 1}`, `5`, and `[1]` each gave 500 `internal`. `[]` gave 201.
- **Suggested fix**: Raise `ValidationError` if `items` is not a non-empty list of dicts. Put the item index in each item error, for example `items[3].dest_country`.
- **Effort**: Small (< 1 hour)

### [Low] The service runs raw SQL, but the README says that only repos call `parcelpost.db`
- **Category**: Architectural fit
- **Location**: parcelpost/services/shipments.py:59-62 (head f93e98f)
- **Problem**: README.md:11 says "Only repos call `parcelpost.db`." The new service code builds `SELECT * FROM shipments WHERE id IN (...)` and calls `db.query` directly. Services can use `db.transaction()` (README.md:10), but SQL belongs in a repo. Other authors will copy this example.
- **Evidence**: The siblings are `parcelpost/repos/shipments.py:5-9` (`get`) and `parcelpost/repos/shipments.py:12-16` (`list_for_customer`).
- **Suggested fix**: Add `get_many(ids)` to `parcelpost/repos/shipments.py`. As an alternative, call `shipments_repo.get` for each id.
- **Effort**: Small (< 1 hour)

### [Low] Retries and bulk label IDs do not appear in the log
- **Category**: Observability
- **Location**: parcelpost/clients/carrier.py:29-32; parcelpost/services/shipments.py:58 (head f93e98f)
- **Problem**: `_post` does not log a failed attempt before it retries. Operators cannot see how often retries occur, or which calls they affect. The bulk log line records only a count. The single path logs the `label_id` (`parcelpost/services/shipments.py:32`). The bulk path logs no label IDs, so a reconciliation with the carrier has nothing to start from.
- **Evidence**: `log.info("%s shipments created for customer %s", len(shipment_ids), customer_id)`.
- **Suggested fix**: Log each failed attempt at WARNING level, with the path and the attempt number. Log the label IDs in the bulk path, and also on failure.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Security (injection, secrets)**: The `IN (...)` string contains only `?` characters, and the values are bound parameters. The PR adds no secrets. The new log lines contain only IDs and counts.
- **Best practices & idioms**: The `lambda` in the bulk loop captures `item`, but `with_retry` calls it at once, so there is no late-binding bug. Money values go through `to_cents`, which rejects floats.
- **API shape**: The bulk response `{"items": [...]}` matches the list endpoint (README.md:25), and the errors keep the one error shape (README.md:29).
- **Maintainability**: The PR adds no circular dependencies. The new imports follow the current direction (handlers → services → repos and clients).
- **Performance (database)**: The final `SELECT` uses the primary key. The PR adds no N+1 database queries. The carrier latency and the lock problems are in the findings above.

## Open Questions
1. Does the carrier API have a void or refund call for labels? `tests/conftest.py:19-20` fakes `void_label`, but `CarrierClient` has no such method. The answer decides the fix for the orphaned labels: a void, or a result for each item.
2. Does the carrier `labels` endpoint accept an idempotency key? If it does, a safe retry for `create_label` is possible.
3. How does production serve requests: one thread, many threads, or many processes? The answer decides whether the bulk lock causes "database is locked" (many processes), "cannot start a transaction within a transaction" (many threads on the shared connection), or only slow requests (one thread).
4. Which call site did not use `with_retry`? The description says that one exists, but no such call site is in this repo. If it is in a different repo, name it.
5. What is the correct maximum batch size? A product owner or the customer contract must give the limit.
6. Does a system outside this repo read `shipment_events`? The answer decides how urgent the backfill is for bulk rows without events.