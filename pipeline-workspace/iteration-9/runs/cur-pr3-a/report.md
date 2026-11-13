# PR Independent Review

## PR
acme/parcelpost#3 — Bulk shipment creation and uniform carrier retries
https://github.com/acme/parcelpost/pull/3
7 files changed, +153/-7 lines, head f93e98f

## What this PR does (from the diff)
The PR adds a `POST /shipments/bulk` route. For each item, the route calls the carrier for a rate and a label, and it inserts all rows inside one database transaction. The PR also adds a retry loop with linear backoff to `CarrierClient._post`. Two new environment variables configure the loop. All carrier calls now retry on `URLError`, and this includes `create_label`.

## What the author says it does
The author says that the bulk endpoint creates many shipments for one customer, with the same fields and validation as `POST /shipments`. The author says that one transaction makes the batch all-or-nothing. The author also says that retries now live in `CarrierClient._post`, so all carrier calls get them, and that there is "No change to single shipment creation."

## The underlying problem
Customers need to create many shipments in one request. The PR also states a second problem: some carrier calls get no retries. At base `c386fca`, the two `get_rate` call sites already use `with_retry` (`parcelpost/services/quotes.py:8`, `parcelpost/services/shipments.py:19`). The only call without a retry is `create_label`, and a comment says that this is intentional (`parcelpost/services/shipments.py:20-21`).

The minimal shape has three parts:
- a bulk handler that reuses the item parse from `create_shipment`
- a service function that reuses the single-create flow: carrier calls first, then one short transaction for rows and events
- a `get_many` function in `parcelpost/repos/shipments.py`

The minimal shape needs no change to the client. The bulk half of the diff is about the same size as this shape. The retry half (`parcelpost/clients/carrier.py`, `parcelpost/config.py`, `tests/test_carrier_client.py`) is extra, and it causes the most severe finding.

## Summary
The handler part of the bulk endpoint has the correct structure. But the bulk service calls the carrier inside the database transaction, and it does not write the `labelled` event. The retry change is not neutral: `create_label` now retries, so a 5xx response from the carrier can buy a second label on all shipment paths. The most important finding is the `create_label` retry. Do not merge until the retry half is removed and the bulk transaction is fixed.

## Findings

### [Critical] The retry loop in `_post` also retries `create_label`, which is not idempotent
- **Category**: Correctness
- **Location**: parcelpost/clients/carrier.py:25-32 (head f93e98f)
- **Problem**: The retry loop is in `_post`, and `create_label` calls `_post` (`parcelpost/clients/carrier.py:42`). Each label request can now make up to 3 attempts, because the default is `CARRIER_RETRIES=3`. The base code says that this is not safe: "Not retried: create_label is not idempotent at the carrier, and a retry after a timeout can buy a second label." (`parcelpost/services/shipments.py:20-21`).

  The loop catches `urllib.error.URLError`. `HTTPError` is a subclass of `URLError`, so all non-2xx statuses get a retry, including 500, 502, 503, and 504. After a 504 from a gateway, the carrier can already have bought the label, and the second attempt buys a second label. The row records only the last `label_id`, so the database has no record of the first label. This applies to `POST /shipments` and to `POST /shipments/bulk`.
- **Evidence**:
  ```python
  for attempt in range(self.retries):
      try:
          with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
              return json.loads(response.read())
      except urllib.error.URLError as exc:
  ```
  I ran a probe at the head SHA. In the probe, `urlopen` first raised `HTTPError(504)` and then returned `{"label_id": "L2"}`. `CarrierClient(..., retries=3).create_label(...)` sent 2 label POSTs and returned `L2`. `AbstractHTTPHandler.do_open` also wraps an `OSError` from the send phase in `URLError`. Thus a connection reset that occurs after the carrier gets the body also causes a retry.
- **Suggested fix**: Delete the retry loop from `_post`. Make one attempt only for `create_label`. Do not add a retry to `create_label` until the carrier accepts an idempotency key.
- **Effort**: Small (< 1 hour)

### [High] The PR adds a second retry policy, and it multiplies with `with_retry`
- **Category**: Architectural fit
- **Location**: parcelpost/clients/carrier.py:24-35, parcelpost/config.py:10-11 (head f93e98f)
- **Problem**: `README.md:15` calls `with_retry` "the one retry policy for carrier calls". The PR adds a second policy in `_post`, with linear backoff (0.5 s, then 1.0 s) and no jitter. The PR keeps the `with_retry` calls at `parcelpost/services/quotes.py:8` and `parcelpost/services/shipments.py:19`. It also adds a new `with_retry` call at `parcelpost/services/shipments.py:42-44`. The two policies multiply:
  - If the carrier is unreachable, one `get_rate` makes 3 × 3 = 9 HTTP calls. A probe of `POST /quotes` showed 9 calls and then a 502.
  - The worst-case time for one `get_rate` increases from about 15.6 s to about 50.1 s. Before: 3 × 5 s timeout + up to 0.6 s jitter. After: 9 × 5 s + 3 × 1.5 s sleep + up to 0.6 s jitter.
  - The loop also retries a 4xx status, which does not change on retry. In a probe with `HTTPError(400)`, one `_post` call made 3 attempts. Through `with_retry`, this becomes 9 attempts.
  - The linear backoff has no jitter, so clients that fail at the same time also retry at the same time. `with_retry` uses full jitter to prevent this (`parcelpost/retry.py:29`).
- **Evidence**: `parcelpost/retry.py:10-30` uses exponential backoff, full jitter, and `retry_on=(UpstreamError,)`. After its own loop, `_post` raises `UpstreamError` (`parcelpost/clients/carrier.py:33-35`). Then `with_retry` runs the full loop again.
- **Suggested fix**: Delete the `_post` loop, the `retries` and `backoff_s` parameters, and the `CARRIER_RETRIES` and `CARRIER_BACKOFF_S` settings. Keep `with_retry` at the call sites. If you want the retry in one place, call `with_retry` inside `CarrierClient.get_rate` only. In the same change, remove the call-site wrappers.
- **Effort**: Small (< 1 hour)

### [High] The bulk service calls the carrier inside the database transaction
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:40-56 (head f93e98f)
- **Problem**: The bulk service calls `get_rate` and `create_label` for each item inside `db.transaction()`. This has two effects:
  - **The rollback removes rows but not labels.** If item k fails, the labels for items 1 to k-1 stay bought at the carrier. No row, no event, and no log line records them. In a probe, a batch of 3 items failed `get_rate` on item 3. The response was 502 with 0 rows, but `FakeCarrier.labels == 2` and `voided == []`.
  - **The transaction stays open for 2 × N network calls.** The transaction starts with `BEGIN IMMEDIATE` (`parcelpost/db.py:39`), and the app has one shared connection (`parcelpost/db.py:13`, `check_same_thread=False`). In a probe, a second thread sent `POST /shipments` during a bulk request. It failed with `sqlite3.OperationalError: cannot start a transaction within a transaction` and returned 500. That request had already bought its label, so it also left a label with no row.

  The shared connection is older than this PR. The PR makes its effect worse. At base, the only transaction holds 2 inserts. Now one transaction can hold the lock for the full batch. With the new retry policy, one item can hold the lock for about 50 s when the carrier is unreachable.
- **Evidence**: The single-create path does the opposite on purpose. It calls the carrier first and then opens a short transaction for one row and one event (`parcelpost/services/shipments.py:18-30` at base `c386fca`). The test fixture already counts labels and records voids (`tests/conftest.py:9-20`). But `test_bulk_is_all_or_nothing` (`tests/test_bulk.py:29-41`) checks only the `shipments` table.
- **Suggested fix**:
  1. Get all rates first. A rate call does not change data at the carrier.
  2. Buy the labels outside the transaction.
  3. If a label call fails, void the labels that this request already bought. The real client has no `void_label` yet. The fake at `tests/conftest.py:19` shows the expected interface.
  4. Log each `label_id` before the transaction, so an operator can reconcile.
  5. Insert all rows and events in one short transaction.

  If the carrier cannot void a label, remove the all-or-nothing contract. Return one result for each item.
- **Effort**: Medium (hours)

### [High] Bulk shipments have no `labelled` event
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:46-56 (head f93e98f)
- **Problem**: The single-create path keeps an invariant: "The row and its first event commit together, so a shipment never exists without its 'labelled' event." (`parcelpost/services/shipments.py:24-25` at base). The bulk path calls `shipments_repo.insert` but not `shipments_repo.add_event`. Thus all bulk shipments are stored without an event. These rows stay in the database after a fix, and the team must backfill them.
- **Evidence**: The base code calls `shipments_repo.add_event(shipment_id, "labelled")` at `parcelpost/services/shipments.py:30`. `create_shipments_bulk` has no `add_event` call. A probe showed an empty `shipment_events` table after a successful bulk request, and 1 event after a single create.
- **Suggested fix**: Call `shipments_repo.add_event(shipment_id, "labelled")` after each insert. A better fix: put "insert the row and its event" in one helper, and use it in both paths. The duplication finding below describes this helper.
- **Effort**: Small (< 1 hour)

### [High] The PR description does not agree with the diff
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: Five claims in the description are not correct for the diff. A reviewer who trusts the description will not look for the Critical finding.
  - "No change to single shipment creation." In fact, `POST /shipments` now retries `create_label`, and one rate request can make up to 9 calls.
  - "nothing is saved and the customer is not left with half a batch". In fact, the database has no rows, but the carrier keeps the labels for the items before the failure.
  - "Each item takes the same fields as `POST /shipments` and goes through the same validation." In fact, the default for a missing `declared_value` is different. See the `declared_value` finding below.
  - "Retries now live in `CarrierClient._post`". In fact, retries live in `_post` and in `with_retry`, and the PR adds a new `with_retry` call at `parcelpost/services/shipments.py:42`.
  - "A carrier call only survived a transient network error if its call site remembered to wrap it in `with_retry`." In fact, at base the only call without `with_retry` is `create_label`, and a comment gives the reason (`parcelpost/services/shipments.py:20-21`).
- **Evidence**: The findings above and below.
- **Suggested fix**: Fix the code as the other findings describe. Then update the description so that it agrees with the diff.
- **Effort**: Small (< 1 hour)

### [Medium] The bulk path copies the single-create flow, and the copy is already different
- **Category**: Architectural fit
- **Location**: parcelpost/handlers/shipments.py:27-37, parcelpost/services/shipments.py:36-62 (head f93e98f)
- **Problem**: The bulk handler copies the item parse from `create_shipment` (`parcelpost/handlers/shipments.py:10-17`). The bulk service copies the rate, label, and insert steps from `create_shipment` (`parcelpost/services/shipments.py:18-30`). The copies already differ from the originals in two places: the missing `labelled` event and the `declared_value` default. After this PR, each change to shipment creation must be made in two places.
- **Evidence**: Compare `parcelpost/handlers/shipments.py:11,17` with `parcelpost/handlers/shipments.py:35`. Compare `parcelpost/services/shipments.py:26-30` (base) with `parcelpost/services/shipments.py:46-56` (head).
- **Suggested fix**: Add a function `_parse_shipment_item(body) -> dict` to the handler module, and use it in both handlers. Put the "insert the row and its event" step in one service helper, and use it in both service functions.
- **Effort**: Small (< 1 hour)

### [Medium] A bulk item without `declared_value` stores 0, not NULL
- **Category**: Correctness
- **Location**: parcelpost/handlers/shipments.py:35 (head f93e98f)
- **Problem**: If `declared_value` is absent, the single handler stores `None` (`parcelpost/handlers/shipments.py:11,17`). The bulk handler uses `item.get("declared_value", 0)`, so it stores `declared_cents = 0`. "No declared value" and "a declared value of 0.00" are different data, for example for customs or insurance. In a probe, a bulk item had `declared_cents: 0`, and the same single create had `declared_cents: None`.
- **Evidence**: `"declared_cents": to_cents(item.get("declared_value", 0), "declared_value"),`
- **Suggested fix**: Use the logic of the single handler: `to_cents(v, "declared_value") if v is not None else None`. The shared parse helper from the duplication finding also fixes this.
- **Effort**: Small (< 1 hour)

### [Medium] The bulk handler does not check the type or size of `items`
- **Category**: Error handling
- **Location**: parcelpost/handlers/shipments.py:25-29 (head f93e98f)
- **Problem**: The handler loops over `body["items"]` without a type check.
  - For `items` values of `"abc"`, `5`, `[1]`, and `{"carrier": "ups"}`, the probe got 500 `internal` each time. The correct response is 422 `invalid_request`.
  - For `[]`, the response is 201 with `{"items": []}`.
  - The list has no maximum length. One request can keep the transaction open for 2 × N carrier calls (see the transaction finding).
- **Evidence**: `require_fields` calls `body.get(...)` (`parcelpost/validation.py:7`), so an item that is not a dict causes an `AttributeError`. A value that is not iterable causes a `TypeError`. The router returns 500 for both (`parcelpost/http.py:54-58`).
- **Suggested fix**: Raise `ValidationError` in these cases:
  - `items` is not a list
  - `items` is empty
  - `items` has more entries than a fixed maximum
  - an item is not a dict
- **Effort**: Small (< 1 hour)

### [Medium] `CARRIER_RETRIES=0` stops all carrier calls
- **Category**: Correctness
- **Location**: parcelpost/clients/carrier.py:24-35, parcelpost/config.py:10 (head f93e98f)
- **Problem**: With `retries=0`, `range(0)` makes no attempt, so `last` stays `None`. Line 34 then raises `AttributeError: 'NoneType' object has no attribute 'reason'`. The router returns 500 for all quotes and shipments, and the client sends no request. A probe confirmed this.

  The name `retries` makes 0 the obvious value for "no retries". But the value is the total number of attempts, not the number of retries. Also, the default in the constructor is `retries=1`, and the default in the config is 3.
- **Evidence**: `raise UpstreamError(f"carrier call {path} failed after {self.retries} attempts: {last.reason}") from last`
- **Suggested fix**: The fix for the retry-policy finding deletes this code. If the loop stays, rename the setting to `CARRIER_ATTEMPTS`. Reject values less than 1 at startup.
- **Effort**: Small (< 1 hour)

### [Medium] The tests do not cover the paths with the highest risk
- **Category**: Testing
- **Location**: tests/test_bulk.py:29-41, tests/test_carrier_client.py:26-38 (head f93e98f)
- **Problem**: These gaps let the findings above pass the test suite. The 24 tests pass at the head SHA.
  - `test_bulk_is_all_or_nothing` checks only the `shipments` table. It does not check `fake_carrier.labels` (2) or `fake_carrier.voided` (`[]`), so it passes when labels have no row.
  - No test checks the `labelled` event for bulk shipments.
  - No test checks `declared_cents` for a bulk item without `declared_value`.
  - `tests/test_carrier_client.py` tests only `get_rate` with `URLError`. No test makes sure that `create_label` makes one attempt. No test covers `HTTPError` 4xx or 5xx.
  - The bulk tests replace the client with `FakeCarrier` (`tests/conftest.py:29-33`). Thus no test runs `with_retry` and the `_post` loop together, and no test shows the 9 calls.
  - No test sends an `items` value with an incorrect type.
- **Evidence**: `tests/test_bulk.py:40-41` asserts only `response.status == 502` and an empty `shipments` table.
- **Suggested fix**: In the all-or-nothing test, assert that bought labels equal voided labels. Add a test that `create_label` makes one attempt after an `HTTPError(504)`. Add tests for the bulk event, the `declared_cents` default, and an `items` value with an incorrect type.
- **Effort**: Medium (hours)

### [Low] The service runs raw SQL
- **Category**: Architectural fit
- **Location**: parcelpost/services/shipments.py:59-62 (head f93e98f)
- **Problem**: `README.md:11` says "SQL. Only repos call `parcelpost.db`." The bulk service builds and runs a `SELECT ... WHERE id IN (...)` query itself. The query is safe, because it inserts only `?` placeholders into the string.
- **Evidence**: The single path uses `shipments_repo.get` (`parcelpost/services/shipments.py:33` at base). All other SQL is in `parcelpost/repos/shipments.py`.
- **Suggested fix**: Add `get_many(ids)` to `parcelpost/repos/shipments.py`. Call it from the service.
- **Effort**: Small (< 1 hour)

### [Low] The PR puts two unrelated changes together
- **Category**: Scope & PR hygiene
- **Location**: PR-wide
- **Problem**: The bulk endpoint and the client retry policy do not depend on each other. The retry change affects all carrier calls in the service. If the team reverts the retry change, the revert also removes the bulk endpoint.
- **Evidence**: The changes to `parcelpost/clients/carrier.py` and `parcelpost/config.py` do not depend on the bulk endpoint.
- **Suggested fix**: Put the two changes in two PRs. Per the retry-policy finding, the retry PR is possibly not necessary.
- **Effort**: Small (< 1 hour)

## Issues Not Found
I found no issues in Security, Performance (other than the lock time and the unbounded batch above), Best practices & idioms, Observability (other than the missing label log in the transaction finding), API & interface design, and Maintainability. The new endpoint adds attack surface, but it takes the same fields as `POST /shipments`, and the only gap is the missing size limit above.

## Open Questions
- **What is the worker model of the server?** The repo has no server entry point. My probe used two threads on the shared connection. With separate processes, each process has its own connection. A second writer then waits up to the `sqlite3` default timeout of 5 s and fails with `database is locked`. The server entry point and its worker configuration will answer this.
- **Can the carrier void a label, or does it accept an idempotency key?** The answer decides two things. It decides whether bulk can be all-or-nothing (void on failure) or must return one result for each item. It also decides whether `create_label` can ever get a retry. The carrier API documentation will answer this.
- **Which systems read `shipment_events`?** No code in this repo reads the table at base. If a tracking or billing system reads it, the missing event is visible immediately. If not, the missing event is a latent data problem. A list of consumers of the table will answer this.