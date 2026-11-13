# PR Independent Review

## PR
acme/parcelpost#1 — Add shipment refunds
https://github.com/acme/parcelpost/pull/1
13 files changed, +249/-9 lines, head cc3d8f2

## What this PR does (from the diff)
The PR adds a `refunds` table, a repo, a service, a strategy module, and two routes: `POST /shipments/{id}/refund` and `GET /customers/{id}/refunds`. The refund service writes a refund row and sets the shipment status to `refunded`. It then calls a new `void_label` carrier method in a loop of 3 tries. The PR also changes the shipment page query from `id > ?` to `id >= ?`, and it rewrites the pagination test.

## What the author says it does
The author says that the PR adds full and partial refunds. The author says that the amount check makes sure "a shipment can never be refunded for more than it was charged", and that an `Idempotency-Key` makes the endpoint "never refunds twice". The author also says that the PR fixes "an off-by-one in shipment pagination where the shipment at the cursor was skipped", with "No behaviour change to existing endpoints otherwise".

## The underlying problem
Operators cannot give money back for a shipment and cancel its carrier label. Customers cannot see their refunds.

The minimal shape has four parts:
- a `refunds` table
- a repo
- one service function that follows the `with_retry` and `db.transaction()` precedent
- two thin handlers

The diff adds more than this: a strategy class hierarchy, a registry, a config key, a new `utils.py`, a repo function with no caller, and an unrelated pagination change.

## Summary
The PR does not do what its description says. The PR tests pass at head cc3d8f2 (23 passed), but I reproduced these failures at the same head:
- SQL injection in `GET /customers/{id}/refunds`
- refunds that total 5997 cents on a 1999-cent charge
- a pagination loop that does not stop when `limit=1`

The most important finding is the SQL injection. Many of the defects have one cause: the PR does not use the helpers that the repo already has. These helpers are `to_cents`, `with_retry`, `db.transaction()`, and the error mapping in `dispatch`.

## Findings

### [Critical] SQL injection in the refund list lets any caller read any table
- **Category**: Security
- **Location**: parcelpost/handlers/refunds.py:43-45 (head cc3d8f2)
- **Problem**: The handler puts the `status` query value into the SQL text with an f-string. Thus a caller can change the query and read the refunds of other customers and the rows of other tables. The handler also calls `db` directly, but README.md:11 says "Only repos call `parcelpost.db`."
- **Evidence**:
  ```python
  if status:
      sql += f" AND s.status = '{status}'"
  rows = db.query(sql, (customer_id,))
  ```
  Results at the PR head:
  - `GET /customers/1/refunds?status=x' OR '1'='1` returned the refunds of shipments 2 and 4. Customer 2 owns shipment 4.
  - A `status` value with `UNION SELECT ... FROM shipments` returned the `label_id` of every shipment (`LBL-1` to `LBL-4`) in the `reason` field. These shipments belong to both customers.

  Every sibling repo binds values as `?` parameters, for example parcelpost/repos/shipments.py:12-16.
- **Suggested fix**: Move the query to `parcelpost/repos/refunds.py`. Bind `status` as a `?` parameter. Call the repo through the service, as the shipment handlers do (parcelpost/handlers/shipments.py:28-33).
- **Effort**: Small (< 1 hour)

### [Critical] A shipment can get refunds for more than its charge
- **Category**: Correctness
- **Location**: parcelpost/services/refunds.py:21-36; parcelpost/services/refund_strategy.py:22 (head cc3d8f2)
- **Problem**: The strategy compares each request only with `charge_cents`. It does not subtract earlier refunds. The service does not examine whether the shipment status is already `refunded`. Each request with a new key gets a new refund of up to the full charge, so the company loses money on each extra refund.
- **Evidence**: I sent three `POST /shipments/1/refund` calls with no `Idempotency-Key` header. All three returned 201 with `amountCents` 1999. For this shipment, the sum of `refunds.amount_cents` was 5997, and the charge was 1999. The repo already has `Conflict` (409) for this type of rejection, at parcelpost/errors.py:23.
- **Suggested fix**: In one `db.transaction()`, read the sum of the earlier refunds for the shipment. Reject the request if this sum plus the new amount is more than `charge_cents`. Decide whether a `refunded` shipment can get a second refund. If it cannot, raise `Conflict`.
- **Effort**: Small (< 1 hour)

### [High] The refund response breaks the API contract in the README
- **Category**: API & interface design
- **Location**: parcelpost/handlers/refunds.py:9-16, 34-47 (head cc3d8f2)
- **Problem**: `_serialize` returns camelCase keys: `refundId`, `shipmentId`, `amountCents`, `createdAt`. But README.md:28 says "All response keys are `snake_case`. All money fields are integer cents and end in `_cents`." Clients will bind to these keys, so a later fix is a breaking change.

  The list endpoint also returns all of a customer's refunds in one response, with no `limit` or cursor. The sibling list endpoint has `limit`, `after`, and `next_cursor` (README.md:24, parcelpost/services/shipments.py:40-43). If pages are added later, that also is a breaking change.
- **Evidence**: The shipment handlers return the repo row with its snake_case keys (parcelpost/handlers/shipments.py:19, :25).
- **Suggested fix**: Rename the response keys to snake_case: `id`, `shipment_id`, `amount_cents`, `reason`, `created_at`. Give the list endpoint the same `limit`/`after`/`next_cursor` shape as `GET /customers/{id}/shipments`.
- **Effort**: Small (< 1 hour)

### [High] The refund amount uses float math and gives wrong cents
- **Category**: Architectural fit
- **Location**: parcelpost/utils.py:1-3; parcelpost/services/refunds.py:29 (head cc3d8f2)
- **Problem**: `dollars_to_cents` returns `int(float(amount) * 100)`. Float math gives one cent less for many values. The function also accepts a JSON float. It turns bad text into a `ValueError`, and the handler returns that as a 500. The repo already has `money.to_cents` for this job, and its docstring names this exact defect.
- **Evidence**: Results at the PR head:
  - `"19.99"` -> 1998
  - `"0.29"` -> 28
  - `"4.35"` -> 434
  - `"1.15"` -> 114

  A refund request with `amount` `"19.99"` on a 1999-cent shipment stored a refund of 1998.

  Precedent: parcelpost/money.py:6-20 says "Floats are rejected: 19.99 is not representable in binary, and int(19.99 * 100) is 1998." Also, parcelpost/handlers/shipments.py:17 converts money in the handler with `to_cents`.
- **Suggested fix**: Delete `parcelpost/utils.py`. Call `to_cents(amount, "amount")` in the handler. Send integer cents to the service.
- **Effort**: Small (< 1 hour)

### [High] The pagination "fix" breaks correct code and can loop forever
- **Category**: Correctness
- **Location**: parcelpost/repos/shipments.py:14; tests/test_shipments.py:32-43 (head cc3d8f2)
- **Problem**: The old query was correct. `next_cursor` is the id of the last item on the page (parcelpost/services/shipments.py:42), so `id > cursor` starts the next page after that item. With `id >= ?`, each page repeats the last item of the page before it. With `limit=1`, the cursor does not move, and a client that follows `next_cursor` does not stop. This change affects the existing `GET /customers/{id}/shipments` endpoint.
- **Evidence**:
  - The base test `test_pagination_walks_every_shipment_once` fails on the PR head: `assert [1, 2, 2, 3, 3, 4, ...] == [1, 2, 3, 4, 5]`.
  - With `limit=1`, six pages in sequence returned the ids `[1, 1, 1, 1, 1, 1]`.
  - The PR replaced the base test with a test that asserts `second["items"][0]["id"] >= first["next_cursor"]`. That assertion accepts the repeated row.
- **Suggested fix**: Change line 14 back to `id > ?`. Restore the base test. Remove the pagination change from this PR.
- **Effort**: Small (< 1 hour)

### [High] The idempotency key is optional, global, and the database does not enforce it
- **Category**: Correctness
- **Location**: parcelpost/handlers/refunds.py:22; parcelpost/repos/refunds.py:19-22; parcelpost/schema.sql:28 (head cc3d8f2)
- **Problem**: The key has three defects:
  - When the header is absent, the handler makes a random UUID. Thus a client retry with no header refunds again. README.md:25 lists the header without an "optional" note, but it marks the body fields "optional".
  - The lookup does not filter on `shipment_id`. If a client sends the same key for a different shipment, the endpoint returns the first refund with 201. That refund can belong to a different customer, and the second shipment gets no refund.
  - The column has no `UNIQUE` constraint. The lookup at services/refunds.py:21 and the insert at :36 do not occur as one atomic operation. Two concurrent calls with one key can both insert. Also, the column has no index, so each lookup scans the full `refunds` table.
- **Evidence**: A request for shipment 2 (customer 2) with key `same` returned `{'refundId': 1, 'shipmentId': 1, ...}`. That is the refund for the shipment of customer 1. The repo uses database constraints for uniqueness in other tables, for example `label_id TEXT NOT NULL UNIQUE` (parcelpost/schema.sql:5).
- **Suggested fix**: Return 422 when the header is absent. Add `UNIQUE (shipment_id, idempotency_key)`. Find the earlier refund by shipment and key. Do the lookup and the insert in one `db.transaction()`.
- **Effort**: Small (< 1 hour)

### [High] A failed label void returns as success, and the retry blocks for about 21 seconds
- **Category**: Error handling
- **Location**: parcelpost/services/refunds.py:36-43, 49-57 (head cc3d8f2)
- **Problem**: `_void_label` catches every `Exception`, sleeps, and tries again. After the third failure it returns normally. The service then logs success and returns 201. The shipment status is `refunded`, but the label is still valid at the carrier. The code writes no log entry for the failure, so an operator cannot find these labels.

  The loop sleeps for a fixed 2 seconds, and it also sleeps after the last try. The default carrier timeout is 5 seconds (parcelpost/config.py:9). Thus one request can block for about 3 × 5 + 3 × 2 = 21 seconds. The broad catch also retries programming errors, for example a `KeyError`.
- **Evidence**: I used a carrier fake that raises `UpstreamError`. The call made 3 tries, slept `[2, 2, 2]`, returned 201, and left the status as `refunded`.

  README.md:15 says `with_retry` is "the one retry policy for carrier calls". `with_retry` raises the last failure again (parcelpost/retry.py:18-28), and its maximum total sleep at the defaults is 0.6 seconds. parcelpost/services/shipments.py:18-30 calls the carrier first and writes to the database after that.
- **Suggested fix**: Delete `_void_label`. Call `with_retry(lambda: client.void_label(...))` before any database write. Let the `UpstreamError` go to `dispatch`, which returns 502.
- **Effort**: Small (< 1 hour)

### [High] The refund handler breaks the error contract and sends internal messages to the client
- **Category**: Error handling
- **Location**: parcelpost/handlers/refunds.py:21-31 (head cc3d8f2)
- **Problem**: The handler catches every `Exception` and returns status 500 with `{"error": str(e)}`. This has three effects:
  - A validation error (422) and a missing shipment (404) both become 500.
  - The body does not have the one error shape that README.md:30 specifies: `{"error": {"code": "...", "message": "..."}}`.
  - The client gets the raw exception text, which can contain SQL. The handler writes no log entry.
- **Evidence**: Results at the PR head:
  - `amount` `"50.00"` -> `500 {'error': 'amount exceeds the shipment charge'}`
  - shipment 999 -> `500 {'error': 'shipment 999 does not exist'}`
  - `amount` `"abc"` -> `500 {'error': "could not convert string to float: 'abc'"}`
  - `reason` `null` -> `500 {'error': 'NOT NULL constraint failed: refunds.reason'}`

  `dispatch` already maps each `AppError` to its status and code. It also hides the messages of unexpected exceptions on purpose (parcelpost/http.py:50-58). No sibling handler has a `try` block (parcelpost/handlers/shipments.py:7-33).
- **Suggested fix**: Delete the `try`/`except`. Parse the id with `parse_positive_int(request.params["id"], "id")`, as line 36 does. Validate that `reason` is a string.
- **Effort**: Small (< 1 hour)

### [Medium] The refund writes are not in a transaction
- **Category**: Architectural fit
- **Location**: parcelpost/services/refunds.py:36-40 (head cc3d8f2)
- **Problem**: The refund insert, the status update, and the event insert are three separate statements. If the second or third statement fails, the refund row stays, but the status change or the `refunded` event is absent. A retry with the same key then gets the stored refund at lines 21-23, so the service never writes the event.
- **Evidence**: parcelpost/services/shipments.py:24-30 writes the shipment row and its first event in `db.transaction()`. Its comment says "The row and its first event commit together". README.md:10 says "Services own transactions".
- **Suggested fix**: Put these steps in one `with db.transaction():` block:
  - the idempotency lookup
  - the check of the refund total
  - the insert
  - `set_status`
  - `add_event`
- **Effort**: Small (< 1 hour)

### [Medium] The strategy hierarchy, registry, config key, and sort helper have no second user
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refund_strategy.py:1-37; parcelpost/config.py:10; parcelpost/repos/refunds.py:4, 25-31 (head cc3d8f2)
- **Problem**: This code has no second user:
  - `RefundStrategy` is an abstract base class with one subclass. `_REGISTRY` has one entry. `REFUND_STRATEGY` has one valid value.
  - The PR description gives only "so we can add other policies later" as the reason. It names no second policy.
  - The config key adds a new failure mode. The app starts with a wrong value, and then every refund fails at request time. `get_strategy` raises a plain `ValueError` (line 37), so `dispatch` returns 500.
  - `list_for_shipment` and `SORT_COLUMNS` have no caller in the PR.
- **Evidence**: The real logic is 6 lines (parcelpost/services/refund_strategy.py:18-24). The repo has no other abstract base class. Other policies in the repo are plain functions, for example the retry policy (parcelpost/retry.py:10-30).
- **Suggested fix**: Move the 6 lines into a plain function in `parcelpost/services/refunds.py`. Delete `refund_strategy.py` and the `refund_strategy` setting. Delete `list_for_shipment` and `SORT_COLUMNS`. Add an abstraction when a second policy exists.
- **Effort**: Small (< 1 hour)

### [Medium] The refund tests mock every collaborator and cannot find the defects above
- **Category**: Testing
- **Location**: tests/test_refunds.py:9-44 (head cc3d8f2)
- **Problem**: Two tests patch both repos and the carrier client, and then they only assert that the mocks got calls. No test sends a request through `dispatch`. No test covers these paths:
  - the SQL in the list endpoint
  - a second refund on one shipment
  - a decimal string amount
  - the error status codes
  - a failed void
  - the rejection branches of `compute` (`<= 0` and `> charge_cents`)

  `test_get_strategy` ends with `assert MagicMock() is not None` (line 44). This line does not test the code.
- **Evidence**: The sibling tests call `dispatch` against an in-memory database (tests/test_shipments.py:5-29). The conftest already has a fake `void_label` that records each voided label in `fake.voided` (tests/conftest.py:19-20). The PR tests do not use it.
- **Suggested fix**: Write the refund tests through `dispatch` with the current fixtures. Add one test for each finding above. Delete line 44.
- **Effort**: Medium (hours)

### [Medium] The PR description makes claims that the diff does not support
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: Five claims in the description are false or only partly true:
  - "a shipment can never be refunded for more than it was charged": This is false. See the over-refund finding.
  - "never refunds twice": This is true only when the client sends the same header for the same shipment. See the idempotency finding.
  - "Voids the label with the carrier, with retries": A failed void returns as success. See the label void finding.
  - "fixes an off-by-one in shipment pagination": The old code was correct, and the change adds a defect.
  - "No behaviour change to existing endpoints otherwise": The pagination change alters `GET /customers/{id}/shipments`.
- **Evidence**: The findings above contain the reproductions.
- **Suggested fix**: Correct the description after you apply the fixes. Remove the pagination claim when you remove the pagination change.
- **Effort**: Small (< 1 hour)

### [Low] The new service has narrating comments, a banner, and an unused variable
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refunds.py:13-15, 19-42, 52 (head cc3d8f2)
- **Problem**: These lines add text but no information:
  - a banner comment block at lines 13-15
  - comments that repeat the next line, for example `# Get the shipment` above `shipments_repo.get(shipment_id)` (lines 20, 25, 28, 31, 35, 38, 42)
  - docstrings that repeat the function name, for example `"""Refund a shipment."""` (line 19, line 50; parcelpost/services/refund_strategy.py:7, :33; parcelpost/utils.py:2)
  - the loop variable `attempt` at line 52, which the loop never reads
- **Evidence**: Sibling services use comments only to give a reason, for example the "Not retried" comment at parcelpost/services/shipments.py:20-21.
- **Suggested fix**: Delete these comments and the docstrings that repeat names. Keep the comments that give a reason.
- **Effort**: Small (< 1 hour)

## Issues Not Found
Best practices & idioms, Performance, Observability, Maintainability, and Scope have no separate findings. The related problems are in the findings above: the 21-second block, the absent log entry, the full table scan, the unbounded list, and the bundled pagination change. The new `void_label` client method matches its siblings (parcelpost/clients/carrier.py:27-33). The schema change adds only a table and an index with `IF NOT EXISTS`, so a rollback is clean.

## Open Questions
- **Must a partial refund void the label?** For any amount, lines 39-43 set the status to `refunded` and void the label, for example for 500 cents of a 1999-cent charge. A partial refund can be a credit on a shipment that is still in transit. If so, the void cancels a live shipment. The product rule for partial refunds answers this question.
- **Can the carrier safely receive the same `labels/void` call two times?** A retry after a timeout can send a second void. The codebase does not retry `create_label` for this reason (parcelpost/services/shipments.py:20-21). The carrier API documentation answers this question.
- **Does the server handle requests concurrently?** `db.connect` uses `check_same_thread=False` (parcelpost/db.py:13), which suggests more than one thread. If so, the race in the idempotency finding causes real double refunds. The deployment model (the number of threads and processes) answers this question.