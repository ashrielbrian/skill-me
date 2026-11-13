Review of acme/parcelpost#1, "Add shipment refunds". The environment has no `pytest`, so I did not run the test suite. I reproduced each runtime finding with a standard-library script against the PR head. I removed the temporary worktree, the review branch, and the bytecode files that my script created.

# PR Independent Review

## PR
acme/parcelpost#1 — Add shipment refunds
https://github.com/acme/parcelpost/pull/1
13 files changed, +249/-9 lines, head cc3d8f2

## What this PR does (from the diff)
The PR adds a `refunds` table, a repo, a service, a strategy module, and a handler module with two routes: `POST /shipments/{id}/refund` and `GET /customers/{id}/refunds`. The service writes a refund row, sets the shipment status to `refunded`, and adds an event. Then it calls a new `CarrierClient.void_label` in a local retry loop. The PR also changes the shipment list query from `id > ?` to `id >= ?` and rewrites the pagination test.

## What the author says it does
The author says that the PR adds full and partial refunds and a list of refunds for each customer. The author makes these claims:
- The amount is validated, "so a shipment can never be refunded for more than it was charged".
- A retry with the same `Idempotency-Key` "never refunds twice".
- The label void has retries.
- `RefundStrategy` exists "so we can add other policies later".
- The PR fixes "an off-by-one in shipment pagination where the shipment at the cursor was skipped".
- "No behaviour change to existing endpoints otherwise."

## The underlying problem
An operator must refund all or part of a shipment charge, void the label at the carrier, and see the refunds for a customer. The minimal change has these parts:
- a `refunds` table and a repo
- one service function that uses `money.to_cents`, `db.transaction()`, and `with_retry`
- one handler module that follows `handlers/shipments.py`
- `CarrierClient.void_label`
- tests through `dispatch` that use the `FakeCarrier` in `tests/conftest.py`

The PR has this core. It also adds a strategy ABC with a registry and a config key, a new `utils.py`, a repo function that nothing calls, and an unrelated change to shipment pagination.

## Summary
The implementation does not match the intent, and I do not recommend a merge in this state. The most important finding is a SQL injection in `GET /customers/{id}/refunds`. One query string reads the refunds of every customer and the database schema. The two main safety claims are also false, and the pagination "fix" breaks an existing endpoint that was correct.

## Findings

### [Critical] SQL injection in the `status` filter of the refund list
- **Category**: Security
- **Location**: parcelpost/handlers/refunds.py:43-45 (head cc3d8f2)
- **Problem**: The handler puts the `status` query value directly into the SQL text, so any caller can change the query. The caller can read the refunds of all customers and any table in the database. `sqlite3` runs only one statement for each `execute`, so a caller cannot add a second statement. But `UNION SELECT` works.
- **Evidence**:
  ```python
  if status:
      sql += f" AND s.status = '{status}'"
  rows = db.query(sql, (customer_id,))
  ```
  I sent these requests to the PR code. It used an in-memory database with two customers and one refund for each:
  - `status=refunded` for customer 1 returned shipment IDs `[1]`.
  - `status=x' OR '1'='1` for customer 1 returned shipment IDs `[1, 2]`. Shipment 2 belongs to customer 2.
  - `status=x' UNION SELECT 1, 1, 1, sql, 'k', name FROM sqlite_master WHERE type='table' --` returned the `CREATE TABLE` text of `refunds`, `shipment_events`, and `shipments` in the `reason` field.

  The handler also calls `db.query` directly. The README says "Only repos call `parcelpost.db`." and "A handler validates input, calls one service function, and shapes the response." The sibling list route at `parcelpost/handlers/shipments.py:28-33` calls `shipments_service.list_shipments`. That function calls `parcelpost/repos/shipments.py:12-16`, which uses only bound parameters.
- **Suggested fix**: Move the query to a new `list_for_customer` function in `repos/refunds.py`. Bind `status` as a `?` parameter. Call the repo through a service function, as `handlers/shipments.py:28-33` does. Think about a deletion of the filter (see Open Questions). Add a test that sends a quote character in `status`.
- **Effort**: Small (< 1 hour)

### [High] `dollars_to_cents` duplicates `money.to_cents` with float math and stores the wrong amount
- **Category**: Architectural fit
- **Location**: parcelpost/utils.py:1-3, parcelpost/services/refunds.py:29 (head cc3d8f2)
- **Problem**: The PR adds a new module with a float conversion: `int(float(amount) * 100)`. Float math cuts many decimal amounts down by one cent. The function also accepts a JSON float, but the README documents `amount` as a "decimal string". An amount that is not valid raises a raw `ValueError`, not a `ValidationError`. The client then gets a 500 (see the error-handling finding).
- **Evidence**:
  ```python
  def dollars_to_cents(amount) -> int:
      """Convert a dollar amount to cents."""
      return int(float(amount) * 100)
  ```
  The repo already has `to_cents` at `parcelpost/money.py:6-20`. Its docstring at `parcelpost/money.py:9-10` names this exact defect: "Floats are rejected: 19.99 is not representable in binary, and int(19.99 * 100) is 1998." The sibling handler calls it at `parcelpost/handlers/shipments.py:17`. The README names `parcelpost/money.py` as the place for "Conversion between decimal strings and integer cents." I reproduced these results:
  - `amount: "19.99"` stored a refund of 1998 cents.
  - `amount: 0.29` (a JSON float) returned 201 and stored 28 cents.
  - `amount: "abc"` returned 500 with `could not convert string to float: 'abc'`.
- **Suggested fix**: Delete `parcelpost/utils.py`. Call `to_cents(amount, "amount")` in the handler, as `handlers/shipments.py:17` does. Give integer cents to the service.
- **Effort**: Small (< 1 hour)

### [High] `_void_label` uses its own retry loop and hides a failed void
- **Category**: Architectural fit
- **Location**: parcelpost/services/refunds.py:42-43, 49-57 (head cc3d8f2)
- **Problem**: The loop catches every `Exception`, sleeps 2 s, and tries again, for a total of 3 attempts. After the last failure, it returns normally. It does not log and it does not raise. The refund returns 201 and the shipment shows `refunded`, but the label stays valid at the carrier. Nobody can find these shipments later, because no log line or event records the failure.

  The loop also sleeps after the last attempt. It blocks the request for 6 s when the carrier fails at once. With `carrier_timeout_s` at its default of 5 s, the worst case is 3 × (5 s + 2 s) = 21 s. The loop variable `attempt` has no use.
- **Evidence**:
  ```python
  for attempt in range(3):
      try:
          client.void_label(shipment["carrier"], shipment["label_id"])
          return
      except Exception:
          time.sleep(2)
  ```
  The README says that `parcelpost/retry.py` holds "`with_retry`, the one retry policy for carrier calls." `with_retry` (`parcelpost/retry.py:10-30`) retries only `UpstreamError` and uses jitter from a 0.2 s base. It raises the last failure "so a caller never mistakes an exhausted retry for success." The siblings call it at `parcelpost/services/quotes.py:8` and `parcelpost/services/shipments.py:19`. In my run, the carrier raised `UpstreamError` on each call. The result was status 201, 6.0 s elapsed, no void, and shipment status `refunded`.
- **Suggested fix**: Call `with_retry(lambda: client.void_label(shipment["carrier"], shipment["label_id"]))`. Decide what a failed void must do (see Open Questions). One choice is to void the label before the database writes. Then the `UpstreamError` returns 502 and no refund row exists. If the refund must stay, record a `void_failed` event and log the error.
- **Effort**: Small (< 1 hour)

### [High] Refund responses use camelCase keys and break the documented API contract
- **Category**: API & interface design
- **Location**: parcelpost/handlers/refunds.py:9-16 (head cc3d8f2)
- **Problem**: `_serialize` returns `refundId`, `shipmentId`, `amountCents`, and `createdAt`, and both new routes use it. Clients will write code against these names. A later change to `snake_case` will then break those clients.
- **Evidence**: The README says: "All response keys are `snake_case`. All money fields are integer cents and end in `_cents`." `amountCents` breaks both rules. The sibling handlers return the repo row with its `snake_case` keys: `parcelpost/handlers/shipments.py:19` and `parcelpost/handlers/shipments.py:25`. The error body `{"error": str(e)}` also breaks the README rule "Errors have one shape: `{"error": {"code": "...", "message": "..."}}`" (see the error-handling finding).
- **Suggested fix**: Use `snake_case` keys: `id`, `shipment_id`, `amount_cents`, `reason`, `created_at`. Do this before any client uses the route.
- **Effort**: Small (< 1 hour)

### [High] Nothing limits the total refund for a shipment
- **Category**: Correctness
- **Location**: parcelpost/services/refund_strategy.py:17-24, parcelpost/services/refunds.py:26-40 (head cc3d8f2)
- **Problem**: `StandardRefundStrategy.compute` compares one request with the full charge. It does not subtract earlier refunds. The service does not check if the shipment status is already `refunded`. Each new request with a new key refunds again. The PR description claims the opposite.
- **Evidence**: The claim is: "The amount is validated, so a shipment can never be refunded for more than it was charged." I reproduced these results on one shipment with `charge_cents` 1999:
  - Key `k1`, amount `"15.00"`: 201, 1500 cents.
  - Key `k2`, amount `"15.00"`: 201, 1500 cents.
  - No key, no amount: 201, 1999 cents.
  - No key, no amount: 201, 1999 cents.
  - The `refunds` table then held 6998 cents for a charge of 1999 cents.
- **Suggested fix**: Open one `db.transaction()` (`BEGIN IMMEDIATE`). In it, read `SUM(amount_cents)` for the shipment. If the new total is more than `charge_cents`, raise `Conflict` (409). Then insert. For a request without `amount`, refund the charge minus earlier refunds. Add a test for each rejected case.
- **Effort**: Small (< 1 hour)

### [High] The idempotency key does not prevent a second refund
- **Category**: Correctness
- **Location**: parcelpost/handlers/refunds.py:22, parcelpost/services/refunds.py:21-23, parcelpost/schema.sql:28 (head cc3d8f2)
- **Problem**: The idempotency check fails in three ways:
  1. A request without the header gets a new `uuid4` key. A client that retries after a timeout gets a second refund. The README lists `Idempotency-Key` as a header of the route, but the code makes it optional and gives no error.
  2. The key is global, not tied to one shipment. A request for shipment 2 with a key from shipment 1 returns the refund of shipment 1 with 201. Shipment 2 gets no refund, but the client sees success.
  3. The check and the insert are separate statements, and the column has no `UNIQUE` constraint. Two concurrent requests with the same key can both pass the check, and then both insert. `db.connect` sets `check_same_thread=False` (`parcelpost/db.py:13`), so concurrent requests can share the connection.

  I reproduced cases 1 and 2. I did not run case 3. It follows from the code: the lookup at line 21 and the insert at line 36 do not share a transaction.

  The table is new, so a constraint costs nothing now. After a deploy, `CREATE TABLE IF NOT EXISTS` does not change the table that is already there. A later fix will then need a migration.
- **Evidence**:
  ```python
  idempotency_key = request.headers.get("Idempotency-Key") or str(uuid.uuid4())
  ```
  ```sql
  idempotency_key TEXT    NOT NULL,
  ```
  Two requests without the header each returned 201 with 1999 cents. A request for shipment 2 with key `same` returned 201 with `shipmentId` 1. The table then held 0 refunds for shipment 2. The PR description claims: "a retry with the same `Idempotency-Key` returns the original refund and never refunds twice."
- **Suggested fix**:
  - Reject a request without `Idempotency-Key` with `ValidationError` (422).
  - Add a `UNIQUE` constraint on `idempotency_key`.
  - If the stored refund for a key has a different `shipment_id`, raise `Conflict` (409).
  - Do the lookup and the insert in one `db.transaction()`.
- **Effort**: Small (< 1 hour)

### [High] The pagination "fix" breaks an existing endpoint, and the PR weakens the test that catches it
- **Category**: Correctness
- **Location**: parcelpost/repos/shipments.py:14, tests/test_shipments.py:32-43 (head cc3d8f2)
- **Problem**: `next_cursor` is the ID of the last item on the page (`parcelpost/services/shipments.py:42`). The old query `id > ?` started after that item, which is correct. The new query `id >= ?` returns that item again at the top of each next page. With `limit=1`, the cursor never moves, so a client that follows `next_cursor` loops forever. This change applies to `GET /customers/{id}/shipments`, an endpoint that already exists. It has no relation to refunds.
- **Evidence**:
  ```diff
  -        "SELECT * FROM shipments WHERE customer_id = ? AND id > ? ORDER BY id LIMIT ?",
  +        "SELECT * FROM shipments WHERE customer_id = ? AND id >= ? ORDER BY id LIMIT ?",
  ```
  I walked all pages over 5 shipments for one customer and got these results:
  - Base `c386fca`, `limit=1` and `limit=2`: `[1, 2, 3, 4, 5]`.
  - PR, `limit=2`: `[1, 2, 2, 3, 3, 4, 4, 5, 5]`.
  - PR, `limit=1`: `[1, 1, 1, 1, 1, 1, 1, 1, 1, 1]` after 10 pages, and the cursor stayed at 1.

  The old test `test_pagination_walks_every_shipment_once` asserted `seen == [1, 2, 3, 4, 5]`, and it fails on the PR code. The PR replaces it with `test_pagination_returns_pages`. The new test asserts only `second["items"][0]["id"] >= first["next_cursor"]`, so it passes with the duplicate.
- **Suggested fix**: Revert both hunks. If the author has a real pagination defect, send it in a separate PR with a test that fails before the fix.
- **Effort**: Small (< 1 hour)

### [High] `create_refund` turns every error into a 500 and sends the raw exception text to the client
- **Category**: Error handling
- **Location**: parcelpost/handlers/refunds.py:21-31 (head cc3d8f2)
- **Problem**: The broad `except Exception` catches each `AppError` before `dispatch` can map it. Validation errors and a shipment that does not exist return 500, not 422 or 404. Clients cannot tell their own error from a server fault. A client that retries on 5xx will retry requests that can never succeed. The body `{"error": str(e)}` does not use the documented error shape. It also sends the raw message of an unexpected exception to the client, and nothing logs that exception.
- **Evidence**: `dispatch` already does this work at `parcelpost/http.py:52-58`. The comment there says: "The message of an unexpected exception can carry SQL or upstream payloads, so the client gets a fixed string and the log gets the rest." The sibling handlers have no `try` (`parcelpost/handlers/shipments.py:7-33`). This handler uses `int(request.params["id"])`, but the siblings use `parse_positive_int` (`parcelpost/handlers/shipments.py:24`). I reproduced these results:
  - Amount over the charge: 500 `{'error': 'amount exceeds the shipment charge'}`.
  - Amount `"-1"`: 500 `{'error': 'amount must be positive'}`.
  - Shipment 99: 500 `{'error': 'shipment 99 does not exist'}`.
  - ID `abc`: 500 `{'error': "invalid literal for int() with base 10: 'abc'"}`.
- **Suggested fix**: Delete the `try`/`except`. Parse the ID with `parse_positive_int`. Let `dispatch` map the errors.
- **Effort**: Small (< 1 hour)

### [Medium] The strategy ABC, registry, and config key serve one policy, and nothing calls `list_for_shipment`
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refund_strategy.py:1-37, parcelpost/config.py:10, parcelpost/repos/refunds.py:4, 25-31 (head cc3d8f2)
- **Problem**: The PR adds these parts for one refund policy:
  - an abstract base class
  - one subclass
  - a registry with one entry
  - a lookup function
  - a `REFUND_STRATEGY` environment variable with one valid value

  The PR description gives only "so we can add other policies later" and names no second policy. This machinery has a cost now. A wrong `REFUND_STRATEGY` value does not stop the service at startup. Instead, each refund request raises `ValueError`, and the client gets a 500. Also, nothing in the PR calls `list_for_shipment` or uses `SORT_COLUMNS`.
- **Evidence**: The PR adds `_REGISTRY: dict[str, type[RefundStrategy]] = {"standard": StandardRefundStrategy}`. `parcelpost/services/refunds.py:32` calls `get_strategy(settings.refund_strategy)` on each request. A grep for `list_for_shipment` and `SORT_COLUMNS` finds only their definitions. The sibling services are plain functions: `parcelpost/services/quotes.py:6-9` and `parcelpost/services/shipments.py:11-43`.
- **Suggested fix**: Replace the module with one function in `services/refunds.py`, for example `_refund_cents(shipment, requested_cents, refunded_cents) -> int`. Delete the `refund_strategy` setting. Delete `list_for_shipment` and `SORT_COLUMNS`. Add an abstraction when a second policy exists.
- **Effort**: Small (< 1 hour)

### [Medium] `GET /customers/{id}/refunds` has no pagination
- **Category**: API & interface design
- **Location**: parcelpost/handlers/refunds.py:34-47 (head cc3d8f2)
- **Problem**: The route returns all refunds of the customer in one response. The response size grows with the history of the customer, and the PR sets no limit. If a later PR adds a default `limit`, old clients that expect a full list will not see the rows after the limit.
- **Evidence**: The sibling list route takes `limit` (default 20) and `after`, and it returns `next_cursor`. See `parcelpost/handlers/shipments.py:28-33`, `parcelpost/validation.py:22-25`, and `parcelpost/services/shipments.py:40-43`.
- **Suggested fix**: Use the same `limit`, `after`, and `next_cursor` contract as the shipment list. Use the `id > ?` form.
- **Effort**: Small (< 1 hour)

### [Medium] The refund writes do not share a transaction
- **Category**: Correctness
- **Location**: parcelpost/services/refunds.py:36-40 (head cc3d8f2)
- **Problem**: `db.connect` opens the connection with `isolation_level=None` (`parcelpost/db.py:12-13`), so each statement commits alone. The service does three writes: the refund insert, `set_status`, and `add_event`. Assume that the second or third write fails, for example on a locked database. The database then keeps a refund row for a shipment that does not show `refunded` and has no event. The total check and the key check in the High findings also need this transaction to be safe for concurrent requests.
- **Evidence**: The sibling service puts its writes in one transaction at `parcelpost/services/shipments.py:24-30`, with the comment "The row and its first event commit together". The README says "Services own transactions."
- **Suggested fix**: Put the key lookup, the total check, the insert, `set_status`, and `add_event` in one `with db.transaction():` block. Keep the carrier call outside the block, as `services/shipments.py:19-22` does.
- **Effort**: Small (< 1 hour)

### [Medium] The tests mock every collaborator and catch none of the defects in this report
- **Category**: Testing
- **Location**: tests/test_refunds.py:1-44 (head cc3d8f2)
- **Problem**: The two service tests patch `refunds_repo`, `shipments_repo`, and `get_client`. They assert only the calls on the mocks. No test sends a request through `dispatch`. So no test covers the SQL injection, the 500 errors, the camelCase keys, or the float rounding. No test covers a failure path: an amount over the charge, a negative amount, a second refund, a request without the header, or a failed void. `test_get_strategy` ends with `assert MagicMock() is not None`, which tests nothing.
- **Evidence**: The fixtures for real tests are already in the repo. `tests/conftest.py:23-26` gives an in-memory database for each test. `tests/conftest.py:19-20` already has `FakeCarrier.void_label`, which records each voided label in `self.voided`. `tests/test_shipments.py:5-8` shows the `dispatch` pattern.
- **Suggested fix**: Write the refund tests through `dispatch` with the fixtures in `tests/conftest.py`. Assert the status code, the body, and the database state for each case in this report. Delete the tests that use mocks.
- **Effort**: Medium (hours)

### [Medium] The PR description makes claims that the diff does not support
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: A reviewer who trusts the description will approve defects in money handling and a regression in an existing endpoint. Each claim below is false at head cc3d8f2:
  - "a shipment can never be refunded for more than it was charged": nothing limits the total (see the total-refund finding).
  - "never refunds twice": a retry without the header refunds twice, and two concurrent retries with the same key can refund twice (see the idempotency finding).
  - "Voids the label with the carrier, with retries.": the code ignores a failed void (see the `_void_label` finding).
  - "fixes an off-by-one in shipment pagination where the shipment at the cursor was skipped": the shipment at the cursor is the last item of the previous page, so the old code did not skip it.
  - "No behaviour change to existing endpoints otherwise.": `GET /customers/{id}/shipments` now returns duplicates.
  - "Tests added.": the PR also weakens an existing test so that the regression passes.
- **Evidence**: See the findings above. The page walk at base `c386fca` returned `[1, 2, 3, 4, 5]`, so the pagination code was correct before the PR.
- **Suggested fix**: Change the code or the description until each claim is true. Move the pagination change out of this PR.
- **Effort**: Small (< 1 hour)

### [Low] The new service code has comments and docstrings that only repeat the code
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refunds.py:13-15, 19-42 (head cc3d8f2)
- **Problem**: These comments add text but no information, and they will drift from the code. The codebase does not use this style.
- **Evidence**:
  - A banner comment, `# Refund service`, between two `# ====` lines (`services/refunds.py:13-15`).
  - Seven comments that repeat the next line, for example `# Get the shipment` above `shipments_repo.get(shipment_id)` (`services/refunds.py:20, 25, 28, 31, 35, 38, 42`).
  - Docstrings that repeat the name: `"""Refund a shipment."""` (`services/refunds.py:19`), `"""Void the shipment's label with the carrier."""` (`services/refunds.py:50`), `"""Look up a refund strategy by name."""` (`services/refund_strategy.py:33`), and `"""Convert a dollar amount to cents."""` (`utils.py:2`).
  - The loop variable `attempt`, which nothing uses (`services/refunds.py:52`).

  The sibling service uses comments only to give a reason, for example "Not retried: create_label is not idempotent at the carrier" (`parcelpost/services/shipments.py:20-21`) and "The row and its first event commit together" (`parcelpost/services/shipments.py:24-25`).
- **Suggested fix**: Delete the banner and the comments that repeat the code. Keep the comments that give a reason.
- **Effort**: Small (< 1 hour)

## Issues Not Found
Best practices & idioms, Performance, Observability, Maintainability, and Scope & PR hygiene have no separate findings. The `_void_label` finding includes the 21 s worst-case block on the request and the absence of a log line for a failed void. The pagination finding includes the unrelated pagination change in this PR. `CarrierClient.void_label` (`parcelpost/clients/carrier.py:35-36`) follows the sibling methods at `parcelpost/clients/carrier.py:27-33`, and I found no defect there.

## Open Questions
1. **What must happen when the void still fails after all retries?** If a refund must not exist without a void, void the label first and return 502 on failure. If the refund must exist, the service needs a record of the open void and a job that tries it again. The business owner of refunds can answer this. The answer decides the order of operations in `refund_shipment`.
2. **Does the carrier endpoint `labels/void` return a JSON body?** `CarrierClient._post` calls `json.loads(response.read())` at `parcelpost/clients/carrier.py:23`. An empty success body raises `json.JSONDecodeError`, which is not an `UpstreamError`. With the current loop, the service sees a successful void as a failure: it sends the void 3 times, waits 6 s, and then hides the error. With `with_retry`, the error goes to the client as a 500 after the database writes. The carrier API documentation can answer this.
3. **Can a shipment have more than one refund?** If not, a `UNIQUE` constraint on `refunds.shipment_id` is the simplest limit, and the total check is not necessary. The answer decides the fix for the total-refund finding.
4. **What is the `status` filter on `GET /customers/{id}/refunds` for?** The code has two shipment statuses: `labelled` (the default at `parcelpost/schema.sql:10`) and `refunded`. `refund_shipment` sets each refunded shipment to `refunded`. So `status=refunded` returns all rows, and other values return no rows. If the team plans no other status, delete the filter. That deletion also removes the SQL injection.
5. **Who can call `POST /shipments/{id}/refund`?** The service has no authentication or authorization code. That gap is older than this PR, but this route is the first one that sends money back to a customer. If no gateway checks the caller, any client can refund any shipment. The deployment topology answers this.