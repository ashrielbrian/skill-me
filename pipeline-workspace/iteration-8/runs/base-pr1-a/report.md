# PR Independent Review

## PR
acme/parcelpost#1 — Add shipment refunds
https://github.com/acme/parcelpost/pull/1
13 files changed, +249/-9 lines, head cc3d8f2

I read the diff and the full base files. I ran a probe script against the PR head in a temporary worktree, and I removed the worktree after that. pytest is not installed in this environment, so I did not run the test suite. All results marked "confirmed" come from that probe.

## What this PR does (from the diff)
The PR adds a refunds feature:
- a `refunds` table and a repo for it
- a service that gets the amount from a strategy class (selected by config), writes the refund, sets the shipment to `refunded`, and voids the label in a sleep-and-retry loop
- two routes: `POST /shipments/{id}/refund` and `GET /customers/{id}/refunds`

It also changes the shipment pagination query from `id > ?` to `id >= ?`, and it replaces the pagination test with weaker assertions.

## What the author says it does
The author says the PR adds full and partial refunds, with validation that stops a refund above the charge. The author also lists:
- a customer refund list with a filter on shipment status
- idempotency through `Idempotency-Key`
- a label void with retries
- a `RefundStrategy` "so we can add other policies later"

The author also says that it "fixes an off-by-one in shipment pagination" and that there is "No behaviour change to existing endpoints otherwise."

## The underlying problem
An operator must refund all or part of a shipment charge, cancel the label at the carrier, and see past refunds for a customer. The minimal change uses the existing pieces:
- a `refunds` table
- a repo with parameterized queries
- a service that checks the remaining amount inside `db.transaction()` and calls `with_retry` for the void
- a handler that uses `to_cents` and `parse_positive_int`, and that lets the router map errors
- HTTP-level tests with the existing `FakeCarrier`

The diff has about the same handler/service/repo layout as that sketch. It also adds more: a strategy class hierarchy with a registry and a config key, a new `utils.py`, a repo function that nothing calls, and a pagination change that has no relation to refunds.

## Summary
Do not merge the PR in its current state. The file layout is correct, but the implementation breaks many documented rules of the codebase. It has two Critical defects. The first is SQL injection in the list endpoint, which I confirmed with cross-customer reads and `sqlite_master` reads. The second is that refunds have no total limit: three refunds of a 1999-cent shipment gave a sum of 5996 cents. The pagination "fix" also adds a regression to an existing endpoint, and the PR weakens the test that showed it.

## Findings

### [Critical] SQL injection through the `status` query parameter
- **Category**: Security
- **Location**: parcelpost/handlers/refunds.py:43-45 (head cc3d8f2)
- **Problem**: The handler puts the raw `status` query value into the SQL text, so any caller can change the query. I confirmed these results:
  - `status=x' OR 1=1 --` on `/customers/2/refunds` returned the refunds of customer 1. This is a data leak across customers.
  - A `UNION SELECT ... FROM shipments` payload returned the `label_id` of each shipment in the `reason` field.
  - A `UNION SELECT ... FROM sqlite_master` payload returned the names of all tables and indexes.

  sqlite3 `execute` runs one statement only, so a stacked `; DROP TABLE` fails. A caller can still read all data.

  The handler also calls `db.query` directly. README.md:11 says "Only repos call `parcelpost.db`". README.md:9 says a handler "calls one service function". The handler skips the service layer and the repo layer, and these layers are the normal place for a parameterized query.
- **Evidence**:
  ```python
  if status:
      sql += f" AND s.status = '{status}'"
  rows = db.query(sql, (customer_id,))
  ```
  Sibling: parcelpost/repos/shipments.py:12-16 sends all values as `?` parameters. parcelpost/repos/refunds.py:26-30 (in this PR) checks an allowlist before it puts a column name into an f-string.
- **Suggested fix**: Move the query to `repos/refunds.py` as `list_for_customer(customer_id, status=None)`. Bind `status` as a `?` parameter. Add a service function, and make the handler call it. Add an HTTP test with a quote character in `status`.
- **Effort**: Small (< 1 hour)

### [Critical] One shipment can get many refunds, and the total can exceed the charge
- **Category**: Correctness
- **Location**: parcelpost/services/refund_strategy.py:17-24, parcelpost/services/refunds.py:21-43, parcelpost/handlers/refunds.py:22 (head cc3d8f2)
- **Problem**: `StandardRefundStrategy.compute` compares each request only with `charge_cents`. It does not subtract earlier refunds. The service does not read `shipment["status"]`, so a shipment that is already `refunded` gets a second refund. When the client sends no `Idempotency-Key` header, the handler makes a random key. Thus a client retry after a timeout becomes a new refund.

  I confirmed this result: three POST requests to `/shipments/1/refund` all returned 201. Two had an empty body, and one had `"amount": "19.99"`. The `refunds` table then had 3 rows with a sum of 5996 cents, but the charge was 1999 cents. This is a direct loss of money.
- **Evidence**: refund_strategy.py:22 is `if requested_cents > shipment["charge_cents"]:`, and this check has no sum of earlier refunds. handlers/refunds.py:22 is `idempotency_key = request.headers.get("Idempotency-Key") or str(uuid.uuid4())`.
- **Suggested fix**: Open one `db.transaction()` (db.py:36-46, `BEGIN IMMEDIATE`). Inside it, read the sum of earlier refunds for the shipment. Reject an amount above `charge_cents - refunded_cents`. Raise `Conflict` (errors.py:23-25) when no amount remains. Make the `Idempotency-Key` header required, and raise `ValidationError` when it is absent.
- **Effort**: Medium (hours)

### [High] The new response bodies break the documented API contract
- **Category**: API & interface design
- **Location**: parcelpost/handlers/refunds.py:9-16, parcelpost/handlers/refunds.py:34-47, README.md:26 (head cc3d8f2)
- **Problem**: The new endpoints break the API contract in three places:
  - `_serialize` returns camelCase keys: `refundId`, `shipmentId`, `amountCents`, `createdAt`. README.md:28 says "All response keys are `snake_case`. All money fields are integer cents and end in `_cents`." The money key `amountCents` does not end in `_cents`. After clients start to use these keys, a fix becomes a breaking change.
  - `GET /customers/{id}/refunds` has no `limit` and no cursor, so it returns all rows. The sibling list endpoint has `limit` and `after` (handlers/shipments.py:28-33, validation.py:22-25).
  - The `status` filter has no practical use. The service sets every refunded shipment to `refunded` (services/refunds.py:39), and no code sets a different status after that. So `status=refunded` returns all rows, and other values return no rows. I confirmed that `status=labelled` returns `{"items": []}`.
- **Evidence**: Sibling handlers return the repo row without changes, so the keys stay snake_case: handlers/shipments.py:19, handlers/shipments.py:25, services/quotes.py:9 (`amount_cents`).
- **Suggested fix**: Delete `_serialize`, or make it return `id`, `shipment_id`, `amount_cents`, `reason`, `created_at`. Add `limit` and `after` with `parse_limit`, as `list_customer_shipments` does. Remove the `status` filter until there is a real need for it.
- **Effort**: Small (< 1 hour)

### [High] `dollars_to_cents` duplicates `money.to_cents` and adds back the float bug that `to_cents` prevents
- **Category**: Architectural fit
- **Location**: parcelpost/utils.py:1-3, parcelpost/services/refunds.py:29 (head cc3d8f2)
- **Problem**: The PR adds `int(float(amount) * 100)`, which cuts off the fraction of a binary float. I confirmed these results:
  - `"19.99"` gives 1998.
  - `"0.29"` gives 28.
  - `"1.15"` gives 114.
  - `"4.35"` gives 434.

  So a full refund sent as a string is 1 cent short. The function also accepts a JSON float, which the API rejects in other places. For `"abc"`, it raises `ValueError`, and the client gets 500, not 422. The new module `utils.py` has a general name and holds one function. README.md:14 already names `money.py` as the module for this conversion.
- **Evidence**: parcelpost/money.py:6-20 has `to_cents`. Its docstring at money.py:9-10 says "Floats are rejected: 19.99 is not representable in binary, and int(19.99 * 100) is 1998." handlers/shipments.py:17 calls `to_cents(declared, "declared_value")` in the handler. tests/test_shipments.py:23 asserts that a float `declared_value` gives 422.
- **Suggested fix**: Delete `parcelpost/utils.py`. In the handler, call `to_cents(amount, "amount")` when `amount` is not None. Send integer cents to the service.
- **Effort**: Small (< 1 hour)

### [High] The pagination "fix" duplicates rows, and the PR weakens the test that showed the regression
- **Category**: Correctness
- **Location**: parcelpost/repos/shipments.py:14, tests/test_shipments.py:32-43 (head cc3d8f2)
- **Problem**: `next_cursor` is the `id` of the last item on the page (services/shipments.py:42). With `id > ?`, the next page starts after that item, and that was correct. The PR changes the query to `id >= ?`, so each page repeats the last item of the page before it. I confirmed two results at the PR head:
  - With `limit=2` and 5 shipments, a full walk returns `[1, 2, 2, 3, 3, 4, 4, 5, 5]`. At base it returns `[1, 2, 3, 4, 5]`.
  - With `limit=1`, every page returns shipment 1 with `next_cursor` 1. A client that follows `next_cursor` never stops.

  The base test `test_pagination_walks_every_shipment_once` asserted `seen == [1, 2, 3, 4, 5]`, and this change makes it fail. The PR replaces it with `test_pagination_returns_pages`. The new test asserts only `second["items"][0]["id"] >= first["next_cursor"]`, and that assertion is true for the bug. The change has no relation to refunds, but it affects every caller of `GET /customers/{id}/shipments`.
- **Evidence**: The diff changes `AND id > ?` to `AND id >= ?`. See the base test at tests/test_shipments.py:32-43 on `main`.
- **Suggested fix**: Revert repos/shipments.py:14 and restore the original test. If a real skip defect exists, open a separate PR that adds a test that fails on `main`.
- **Effort**: Small (< 1 hour)

### [High] The idempotency check is not atomic, not unique, and not tied to the shipment
- **Category**: Correctness
- **Location**: parcelpost/services/refunds.py:21-40, parcelpost/schema.sql:28 (head cc3d8f2)
- **Problem**:
  - The service reads the key (line 21) and inserts the refund (line 36) as separate statements, outside a transaction. The `idempotency_key` column has no UNIQUE constraint. Two concurrent requests with the same key can both pass the check, and both can insert a refund. db.py:13 opens the connection with `check_same_thread=False`, so the design expects calls from more than one thread.
  - The key lookup does not filter by `shipment_id`. A key that a client uses again on a different shipment returns the first refund with 201, and the second shipment gets no refund. I confirmed it: key `k1` on shipment 3 and then on shipment 4 gave two 201 responses, and both had `shipmentId` 3.
  - The refund insert, `set_status`, and `add_event` (lines 36-40) are three separate auto-commit statements. If line 39 or line 40 fails, a refund row exists with no `refunded` status and no event.
  - The key has no index, so the lookup at line 21 scans the full `refunds` table on each POST.
- **Evidence**: The sibling is services/shipments.py:24-30. It puts the insert and the event in `with db.transaction():`, and its comment says "The row and its first event commit together". db.py:36-46 gives `transaction()` with `BEGIN IMMEDIATE`.
- **Suggested fix**: Add `UNIQUE` to `idempotency_key`, or add `UNIQUE (shipment_id, idempotency_key)`. The constraint also adds the index. Do the key lookup, the limit check, the insert, the status update, and the event in one `db.transaction()`. If the stored refund has a different `shipment_id`, raise `Conflict`.
- **Effort**: Medium (hours)

### [High] The refund handler bypasses the router error mapping and leaks internal messages
- **Category**: Error handling
- **Location**: parcelpost/handlers/refunds.py:21-31, parcelpost/handlers/refunds.py:24 (head cc3d8f2)
- **Problem**: `except Exception as e: return json_response({"error": str(e)}, 500)` catches every `AppError`, so the router never gets it. I confirmed these results:
  - A shipment that does not exist gives 500, not 404: `{'error': 'shipment 99 does not exist'}`.
  - An amount above the charge gives 500, not 422: `{'error': 'amount exceeds the shipment charge'}`.
  - A non-numeric id gives 500 with `"invalid literal for int() with base 10: 'abc'"`. Line 24 uses `int()`, not `parse_positive_int`.
  - `"reason": null` gives 500 with `'NOT NULL constraint failed: refunds.reason'`. This sends database error text to the client.

  The body shape `{"error": "..."}` is also different from the documented shape at README.md:30. The handler does not log the exception, so operators do not see these failures.
- **Evidence**: http.py:50-58 maps an `AppError` to its status and body. For other exceptions, it logs the error and returns a fixed message. The comment at http.py:55-56 says "The message of an unexpected exception can carry SQL or upstream payloads, so the client gets a fixed string and the log gets the rest." No sibling handler has a try/except: handlers/shipments.py:7-33, handlers/quotes.py:6-14.
- **Suggested fix**: Delete the try/except. Use `parse_positive_int(request.params["id"], "id")`. Validate `reason` as a string in the handler. Let the router map the errors.
- **Effort**: Small (< 1 hour)

### [High] `_void_label` hides every carrier failure and does not use `with_retry`
- **Category**: Error handling
- **Location**: parcelpost/services/refunds.py:43, parcelpost/services/refunds.py:49-57 (head cc3d8f2)
- **Problem**: The loop catches `Exception`, sleeps 2 s, and tries again. After 3 failures, it returns as if the void worked. The refund then returns 201, and the label stays valid at the carrier. Thus the customer gets the money back and can still ship on the label. I confirmed it: with a carrier that raises `UpstreamError`, the request returned 201, and the label was not voided. No log line records the failure. The loop has these other effects:
  - It also catches programming errors such as `KeyError`. A bug then looks like a carrier outage, and the outage then looks like success.
  - It sleeps after the last attempt too. In the worst case it blocks the request thread for about 21 s: 3 × 5 s timeout (config.py:9) plus 3 × 2 s sleep.
  - The DB writes occur before the void (lines 36-40), so a void failure cannot roll them back.
  - The loop variable `attempt` has no use.
- **Evidence**: parcelpost/retry.py:10-30 has `with_retry`. It retries only `UpstreamError`, uses exponential backoff with jitter, and raises the last failure again. README.md:15 calls it "the one retry policy for carrier calls". services/quotes.py:8 and services/shipments.py:19 use it.
- **Suggested fix**: Replace `_void_label` with `with_retry(lambda: client.void_label(shipment["carrier"], shipment["label_id"]))`. Void the label first, and then write the refund in one transaction, as services/shipments.py:18-30 does. Before you retry the void, confirm that `labels/void` is idempotent at the carrier (see services/shipments.py:20-21).
- **Effort**: Small (< 1 hour)

### [High] The PR description claims guarantees that the diff does not give
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: Five claims in the description are false at head cc3d8f2:
  1. "a shipment can never be refunded for more than it was charged". This is false. Three refunds gave a sum of 5996 cents on a 1999-cent charge.
  2. "a retry with the same `Idempotency-Key` returns the original refund and never refunds twice". This is true only in part. It fails when the header is absent, when requests are concurrent, and when a client uses the key again on a different shipment.
  3. "Voids the label with the carrier, with retries". The code ignores the last failure and returns 201.
  4. "fixes an off-by-one in shipment pagination where the shipment at the cursor was skipped". The base code did not skip that shipment. The change adds duplicates.
  5. "No behaviour change to existing endpoints otherwise". `GET /customers/{id}/shipments` changes its behavior.

  A reviewer who trusts the description can approve a change that loses money.
- **Evidence**: See the Critical and High findings above.
- **Suggested fix**: Change the code so that claims 1-3 are true. Remove the pagination change, which removes claims 4 and 5. Update the description.
- **Effort**: Small (< 1 hour)

### [High] The tests mock all collaborators and do not test the HTTP contract
- **Category**: Testing
- **Location**: tests/test_refunds.py:9-44 (head cc3d8f2)
- **Problem**: `test_refund_shipment` and `test_refund_is_idempotent` patch `refunds_repo`, `shipments_repo`, and `get_client`. Then they only assert that the mocks got calls. No test sends a request through `dispatch`, and no test uses the real schema or the GET endpoint. So the suite does not find any defect in this report:
  - the SQL injection
  - the camelCase keys
  - the 500 status for 404 and 422 cases
  - the rounding error in cents
  - the repeated refunds
  - the key that a client uses on two shipments
  - the silent void failure

  `test_get_strategy` ends with `assert MagicMock() is not None` (line 44), and this assertion is always true. The PR also weakens the pagination test (see the pagination finding).
- **Evidence**: The sibling tests go through `dispatch` with an in-memory DB: tests/test_shipments.py:5-8 and 11-29, tests/test_quotes.py:8-12. tests/conftest.py:7-33 already gives a `FakeCarrier` with `void_label` and a `voided` list (lines 19-20). The PR does not use it.
- **Suggested fix**: Write the refund tests through `dispatch` with the `fresh_db` and `fake_carrier` fixtures. Add cases for these conditions:
  - status 422 and status 404
  - a quote character in `status`
  - a second refund on the same shipment
  - a request with no `Idempotency-Key`
  - the same key on two shipments
  - `amount: "19.99"`
  - a carrier void failure

  Delete line 44.
- **Effort**: Medium (hours)

### [Medium] `RefundStrategy`, its registry, and `REFUND_STRATEGY` support one policy only
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refund_strategy.py:6-37, parcelpost/config.py:10, parcelpost/services/refunds.py:32-33 (head cc3d8f2)
- **Problem**: The PR adds an abstract base class, one subclass, a registry with one entry, a lookup function, and a config key. Every call uses `"standard"`. If you delete this machinery and keep the three checks in `compute`, nothing breaks today. The design has these other effects:
  - `get_strategy` runs on each request and raises a plain `ValueError` for an unknown name. A bad `REFUND_STRATEGY` value does not stop the start of the service. Every refund then returns 500.
  - The README does not list `REFUND_STRATEGY`.
  - The description says the class exists "so we can add other policies later", but it names no second policy.
  - The `compute` contract gets only the shipment, so it cannot see earlier refunds. The fix for the Critical finding must change the interface anyway.
- **Evidence**: config.py:7-9 holds only infrastructure settings: the DB path, the carrier URL, and the timeout. No business rule in the codebase uses a registry. The quote and shipment services call plain functions: services/quotes.py:6-9, services/shipments.py:11-33.
- **Suggested fix**: Delete `refund_strategy.py` and the `refund_strategy` setting. Put the amount checks in a private function in `services/refunds.py`. Add an abstraction when a second policy exists.
- **Effort**: Small (< 1 hour)

### [Medium] Dead code and comments that repeat the code
- **Category**: Simplicity & economy
- **Location**: parcelpost/repos/refunds.py:4, parcelpost/repos/refunds.py:25-31, parcelpost/services/refunds.py:13-15, parcelpost/services/refunds.py:19-42, parcelpost/services/refunds.py:52 (head cc3d8f2)
- **Problem**:
  - Nothing in the PR calls `list_for_shipment` or uses `SORT_COLUMNS`. Grep finds no caller.
  - services/refunds.py:13-15 is a banner comment: `# Refund service`.
  - The comments at services/refunds.py:20, 25, 28, 31, 35, 38, and 42 repeat the next line. An example is `# Get the shipment` above `shipments_repo.get(shipment_id)`.
  - Some docstrings only repeat the name:
    - `"""Refund a shipment."""` (services/refunds.py:19)
    - `"""Base class for refund strategies."""` (refund_strategy.py:7)
    - `"""Look up a refund strategy by name."""` (refund_strategy.py:33)
    - `"""Convert a dollar amount to cents."""` (utils.py:2)
  - The loop variable `attempt` has no use (services/refunds.py:52).

  These lines make the code longer to read and give no information.
- **Evidence**: The sibling services add a comment only to give a reason: services/shipments.py:20-21 and services/shipments.py:24-25.
- **Suggested fix**: Delete `list_for_shipment` and `SORT_COLUMNS`. Delete the banner, the comments that repeat the code, and the docstrings that repeat the name. Keep comments that give a reason, for example why a carrier call has or has no retry.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Module boundaries and coupling**: The new modules follow the handler/service/repo/client layout. The GET handler is the one exception, and the SQL injection finding covers it. The PR adds no circular import.
- **Carrier client**: `void_label` (clients/carrier.py:35-36) has the same shape as `get_rate` and `create_label` (clients/carrier.py:27-33). Errors go through `_post`, which raises `UpstreamError`.
- **Performance**: The `refunds_shipment` index supports the join and the lookup by shipment. I found no N+1 query. The findings above include the unbounded list, the missing index on the key, and the 21 s blocking void.
- **Reversibility**: The schema change adds one table and one index with `IF NOT EXISTS`. It does not change an existing table, so a rollback is easy.
- **Secrets and crypto**: The PR adds no secrets, keys, or crypto.
- **Observability on success**: The log at services/refunds.py:45 matches the sibling at services/shipments.py:32. The error-handling findings cover the missing logs on failure.
- **Async**: The codebase has no async code, so no event-loop issues apply.

## Open Questions
1. **Does a partial refund cancel the shipment?** The service sets status `refunded` and voids the label for every refund, also for a partial `amount` (services/refunds.py:39-43). So a small partial refund (for example, a fee correction) makes the label unusable. The product owner must say if a partial refund keeps the shipment active. If yes, only a full refund must void the label and change the status.
2. **Is `labels/void` idempotent at the carrier?** The codebase does not retry `create_label` because that call is not idempotent (services/shipments.py:20-21). The carrier API documentation or the carrier team must confirm that a second void is safe before the void uses `with_retry`.
3. **Do all callers of `POST /shipments/{id}/refund` send `Idempotency-Key`?** If an internal tool calls it without the header, a required header breaks that tool. A list of the planned callers answers this question.
4. **Does the HTTP server run handlers on more than one thread?** The repo has no server code. `check_same_thread=False` (db.py:13) suggests concurrent use. The deployment configuration shows how likely the race in the idempotency finding is.
5. **Is there a real pagination defect behind the off-by-one claim?** The base code returns each shipment once in a full walk, with `limit=1` and with `limit=2`. The author must supply a request, a log, or a test that fails on `main` to show the case.