Review target: acme/parcelpost#1, "Add shipment refunds", head `cc3d8f2`.

# PR Independent Review

## PR
acme/parcelpost#1 — Add shipment refunds
https://github.com/acme/parcelpost/pull/1
13 files changed, +249/-9 lines, head cc3d8f2

## What this PR does (from the diff)
The PR adds a `refunds` table, a repo, a service, and two routes: `POST /shipments/{id}/refund` and `GET /customers/{id}/refunds`. The service gets the amount from a `RefundStrategy` class. Then it writes the refund, sets the shipment status to `refunded`, and calls a new `CarrierClient.void_label` in a retry loop. The PR also changes the shipment pagination query from `id > ?` to `id >= ?` and rewrites the pagination test.

## What the author says it does
The author says that the PR adds full and partial refunds, a refund list with a status filter, and an idempotent refund endpoint. The author makes these claims:
- "a shipment can never be refunded for more than it was charged".
- A retry with the same `Idempotency-Key` "never refunds twice".
- The label void has retries.
- The pagination change "fixes an off-by-one in shipment pagination where the shipment at the cursor was skipped".
- "No behaviour change to existing endpoints otherwise."

## The underlying problem
Operators must return all or part of a shipment charge to a customer, cancel the label with the carrier, and see the refunds of a customer. The minimal change has these parts:
- a `refunds` table
- one repo
- one service function that uses `money.to_cents`, `with_retry`, and `db.transaction()`
- two thin handlers
- `CarrierClient.void_label`

The PR has more than this minimal shape. It adds a strategy class hierarchy with a registry and a config key, a new `utils.py`, a repo function with no caller, and a pagination change that has no relation to refunds.

## Summary
The diff does not do what the description claims. Do not merge the PR in this state.

The most important finding is a SQL injection in the `status` filter: any caller can read the refunds of all customers and any table. The refund code also ignores four things that the codebase already has: `money.to_cents`, `with_retry`, `db.transaction()`, and the error mapping in the router. Each gap causes a defect that I measured on the head code. The pagination change breaks `GET /customers/{id}/shipments`.

I confirmed each finding with the head code in a temporary worktree. After the checks, I removed the worktree, the local branch, and my scripts.

## Findings

### [Critical] SQL injection in the `status` filter of `GET /customers/{id}/refunds`
- **Category**: Security
- **Location**: parcelpost/handlers/refunds.py:43-45 (head cc3d8f2)
- **Problem**: The handler puts the raw `status` query value into the SQL text. A caller can read the refunds of all customers. With `UNION`, a caller can read any table in the database. A value that contains a quote, for example `o'brien`, causes a 500.
- **Evidence**:
  ```python
  if status:
      sql += f" AND s.status = '{status}'"
  rows = db.query(sql, (customer_id,))
  ```
  I ran the head code with two customers. Each customer had one refund.
  - `status=refunded` on customer 1 returned shipment ids `[1]`.
  - `status=x' OR '1'='1` on customer 1 returned shipment ids `[1, 2]`. Shipment 2 belongs to customer 2.
  - `status=x' UNION SELECT 1,1,1,name,1,1 FROM sqlite_master --` returned all table and index names in the `reason` field: `refunds`, `refunds_shipment`, `shipment_events`, `shipments`, `shipments_customer`, `sqlite_autoindex_shipments_1`, `sqlite_sequence`.
  - `status=o'brien` returned 500, and the log showed `sqlite3.OperationalError: near "brien": syntax error`.

  All other queries in the repo bind their parameters, for example parcelpost/repos/shipments.py:12-16. The PR itself contains the safe pattern for dynamic SQL, an allowlist, at parcelpost/repos/refunds.py:4, 26-28.
- **Suggested fix**: Append `" AND s.status = ?"` and add `status` to the params tuple. Move the query into `repos/refunds.py` (see the Medium layer finding). Return 422 for a `status` value that is not a known status.
- **Effort**: Small (< 1 hour)

### [High] The refund amount uses float math and does not use `money.to_cents`
- **Category**: Architectural fit
- **Location**: parcelpost/utils.py:1-3, parcelpost/services/refunds.py:29 (head cc3d8f2)
- **Problem**: The new `dollars_to_cents` multiplies a float by 100 and then truncates it. Many common amounts lose one cent. The function also accepts JSON floats, which the API rejects at other endpoints. Bad input such as `"abc"` or `"nan"` raises `ValueError`, so the client gets a 500, not a 422.
- **Evidence**: The function body is `return int(float(amount) * 100)`. I measured these results on the head code:
  - `"19.99"` → 1998
  - `"0.29"` → 28
  - `"1.15"` → 114
  - JSON float `1.15` → accepted, 114
  - A POST with `amount: "19.99"` on a shipment with a 1999-cent charge stored `amountCents: 1998`.

  The repo already has the correct helper. `to_cents` at parcelpost/money.py:6-20 uses `Decimal` with `ROUND_HALF_UP`, rejects floats, and raises `ValidationError`. Its docstring at parcelpost/money.py:9-10 names this exact bug: "int(19.99 * 100) is 1998". README.md:14 names `money.py` as the module for this conversion. The sibling handler uses it at parcelpost/handlers/shipments.py:17.
- **Suggested fix**: Delete `parcelpost/utils.py`. In the handler, call `to_cents(amount, "amount")`, as `handlers/shipments.py:17` does. Pass integer cents to the service. Add one test where `"19.99"` gives 1999. Add one test where a float gives 422.
- **Effort**: Small (< 1 hour)

### [High] The label void hides every failure and does not use `with_retry`
- **Category**: Architectural fit
- **Location**: parcelpost/services/refunds.py:43, 49-57 (head cc3d8f2)
- **Problem**: `_void_label` catches every exception and sleeps 2 seconds. After the third failure, it returns with no error and no log line. The refund row and the `refunded` status stay, but the label stays valid at the carrier, and operators cannot see this. The loop can block the request for up to 21 seconds: 3 timeouts of 5 seconds (the `CARRIER_TIMEOUT_S` default) plus 3 sleeps of 2 seconds. The loop also sleeps after the last attempt.
- **Evidence**:
  ```python
  for attempt in range(3):
      try:
          client.void_label(shipment["carrier"], shipment["label_id"])
          return
      except Exception:
          time.sleep(2)
  ```
  I used a carrier fake that always raised `UpstreamError`. The POST returned 201, the shipment status became `refunded`, and the recorded sleeps were `[2, 2, 2]`.

  README.md:15 calls `with_retry` "the one retry policy for carrier calls". `with_retry` at parcelpost/retry.py:10-30 has these properties:
  - It retries only on `UpstreamError`.
  - It uses exponential backoff with full jitter and a base delay of 0.2 s.
  - It re-raises the last failure. Its docstring says "a caller never mistakes an exhausted retry for success".

  The siblings use it at parcelpost/services/quotes.py:8 and parcelpost/services/shipments.py:19. The new `except Exception` also retries programming errors such as `KeyError`.
- **Suggested fix**: Call `with_retry(lambda: client.void_label(...))`. Let the last `UpstreamError` go to the router, which returns 502. Choose a clear order. One option: void the label first, then write the refund. The other option: write the refund, and on a void failure record a `void_failed` event and call `log.exception`. Delete the unused `attempt` variable.
- **Effort**: Small (< 1 hour)

### [High] Refund responses use camelCase keys and break the API contract
- **Category**: API & interface design
- **Location**: parcelpost/handlers/refunds.py:9-16 (head cc3d8f2)
- **Problem**: `_serialize` returns the keys `refundId`, `shipmentId`, `amountCents`, and `createdAt`. The README contract says that keys are snake_case and that money fields end in `_cents`. Clients will build on these names after release, so a later rename will break them.
- **Evidence**: README.md:26 says "All response keys are `snake_case`. All money fields are integer cents and end in `_cents`." The sibling handlers return the repo row directly, with snake_case keys: parcelpost/handlers/shipments.py:19, 25. In my run, the response was `{'refundId': 1, 'shipmentId': 1, 'amountCents': 1998, 'reason': '', 'createdAt': '...'}`.
- **Suggested fix**: Return snake_case keys: `id`, `shipment_id`, `amount_cents`, `reason`, `created_at`. Keep the projection only to hide `idempotency_key`. Document the response shape in the README row.
- **Effort**: Small (< 1 hour)

### [High] The pagination "fix" breaks an existing endpoint, and the PR weakens the test that found the problem
- **Category**: Intent vs implementation
- **Location**: parcelpost/repos/shipments.py:14, tests/test_shipments.py:32-43 (head cc3d8f2)
- **Problem**: The cursor is the id of the last item that the previous page returned. Thus `id > ?` was correct. With `id >= ?`, each page returns the last item of the previous page again. At `limit=1`, the cursor never changes, so a client that reads pages until `next_cursor` is null never stops. The description says "No behaviour change to existing endpoints otherwise", but this change affects `GET /customers/{id}/shipments`. The change also has no relation to refunds.
- **Evidence**: parcelpost/services/shipments.py:42 sets `next_cursor = items[-1]["id"]`, which is the last returned id. I ran the loop from the deleted test `test_pagination_walks_every_shipment_once` on the head code with 5 shipments:
  - `limit=2`: 5 pages, ids `[1, 2, 2, 3, 3, 4, 4, 5, 5]`. The deleted test expects `[1, 2, 3, 4, 5]`, so it fails on the head code.
  - `limit=1`: I stopped the loop at 20 pages. Every page returned id 1, and `next_cursor` stayed at 1.

  The new assertion at tests/test_shipments.py:43 is `second["items"][0]["id"] >= first["next_cursor"]`. This assertion passes when the first row of the second page repeats the last row of the first page.
- **Suggested fix**: Revert line 14 of `repos/shipments.py`. Restore the original test. If a real skip bug exists, open a separate PR with a test that fails before the fix.
- **Effort**: Small (< 1 hour)

### [High] The total refunds for a shipment can be more than its charge
- **Category**: Correctness
- **Location**: parcelpost/services/refund_strategy.py:17-24, parcelpost/services/refunds.py:18-46 (head cc3d8f2)
- **Problem**: The limit check compares only the current request with the full charge. It does not subtract earlier refunds. The service does not check the shipment status. Each request with a new key, or with no key, makes a new refund. This contradicts the claim "a shipment can never be refunded for more than it was charged."
- **Evidence**: `if requested_cents > shipment["charge_cents"]: raise ValidationError(...)` checks only the current request. In my run, four POSTs to `/shipments/1/refund` all returned 201. The four refunds totaled 7995 cents for a charge of 1999 cents. The carrier fake got four void calls for `LBL-1`.
- **Suggested fix**: In one `db.transaction()`, read `SUM(amount_cents)` for the shipment. Reject the request if that sum plus the new amount is more than `charge_cents`. Return `Conflict` (409, parcelpost/errors.py:23-25) when the shipment already has a full refund. Add a test for a second refund.
- **Effort**: Small (< 1 hour)

### [High] The refund handler catches every exception and returns 500 with a different error shape
- **Category**: Error handling
- **Location**: parcelpost/handlers/refunds.py:21-31 (head cc3d8f2)
- **Problem**: The `except Exception` block changes every error into a 500. Validation errors (422) and missing shipments (404) also become 500. The body `{"error": "<message>"}` does not match the single error shape in the README. The block sends the raw exception text to the client and writes no log line. Because the handler catches the error, the router cannot log it.
- **Evidence**:
  ```python
  except Exception as e:
      return json_response({"error": str(e)}, 500)
  ```
  I measured these results on the head code:
  - `amount: "50"` → 500 `{'error': 'amount exceeds the shipment charge'}`
  - `/shipments/99/refund` → 500 `{'error': 'shipment 99 does not exist'}`
  - `/shipments/abc/refund` → 500 `{'error': "invalid literal for int() with base 10: 'abc'"}`

  The router already maps `AppError` to the correct status. It logs other errors and sends a fixed message to the client (parcelpost/http.py:50-58). Its comment warns that an exception message "can carry SQL or upstream payloads". README.md:28 sets the error shape. The sibling handlers have no try/except (parcelpost/handlers/shipments.py:7-19). Line 24 uses `int()`, but the siblings use `parse_positive_int` (parcelpost/handlers/shipments.py:24).
- **Suggested fix**: Delete the try/except. Parse the id with `parse_positive_int(request.params["id"], "id")`. Let the router map the errors.
- **Effort**: Small (< 1 hour)

### [Medium] The refund list handler does not go through the service and repo layers
- **Category**: Architectural fit
- **Location**: parcelpost/handlers/refunds.py:34-47, parcelpost/repos/refunds.py:25-31 (head cc3d8f2)
- **Problem**: `list_customer_refunds` makes SQL and calls `db.query` in the handler. The README says that only repos call `parcelpost.db`, and that a handler calls one service function. This placement made the SQL injection possible. The endpoint has no `limit` and no cursor, so the response for a customer with many refunds has no size limit. The PR adds the repo function `list_for_shipment` with a safe shape, but no code calls it.
- **Evidence**: README.md:9 says "A handler validates input, calls one service function, and shapes the response." README.md:11 says "Only repos call `parcelpost.db`." The sibling list endpoint uses all three layers and has `parse_limit` and a cursor: parcelpost/handlers/shipments.py:28-33 → parcelpost/services/shipments.py:40-43 → parcelpost/repos/shipments.py:12-16.
- **Suggested fix**: Add `list_for_customer(customer_id, status, limit, after_id)` to `repos/refunds.py`, with bound parameters. Add a service function that returns `items` and `next_cursor`. Make the handler call only that service function. Delete `list_for_shipment` and `SORT_COLUMNS`, or give them a caller.
- **Effort**: Small (< 1 hour)

### [Medium] `RefundStrategy` adds a class hierarchy, a registry, and a config key for one rule
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refund_strategy.py:6-37, parcelpost/config.py:10, parcelpost/services/refunds.py:32 (head cc3d8f2)
- **Problem**: The PR adds an abstract base class, one subclass, a registry with one entry, a lookup function, and the `REFUND_STRATEGY` environment variable. All of this selects one rule of 6 lines. The description says "so we can add other policies later", but it does not name a second policy. The config key becomes a public contract. The service finds a wrong value only at refund time, on each request, and not at start. With the catch-all handler, the client gets 500 `{"error": "unknown refund strategy: ..."}`.
- **Evidence**: `_REGISTRY` at refund_strategy.py:27-29 has one entry, `"standard": StandardRefundStrategy`. The only call site is `get_strategy(settings.refund_strategy)` at services/refunds.py:32. The test at tests/test_refunds.py:42-44 exists only for the registry. If you delete the module and move the three checks into the service, no behavior changes.
- **Suggested fix**: Delete `refund_strategy.py` and the `refund_strategy` setting. Put the amount checks in `refund_shipment`, or in a private function in `services/refunds.py`. Add a strategy type when a second policy exists.
- **Effort**: Small (< 1 hour)

### [Medium] The database does not enforce idempotency, and a client can bypass it easily
- **Category**: Correctness
- **Location**: parcelpost/schema.sql:28, parcelpost/services/refunds.py:21-36, parcelpost/handlers/refunds.py:22 (head cc3d8f2)
- **Problem**: The description says that a retry with the same key "never refunds twice". Four gaps make this claim false or weak:
  1. A request without the header gets a random `uuid4` key. A client retry without the header makes a second refund, and the client gets no error.
  2. `idempotency_key` has no `UNIQUE` constraint. The service reads and then inserts, outside a transaction. Two concurrent requests with one key can both insert a row. `db.connect` sets `check_same_thread=False` (parcelpost/db.py:13), so threads share the connection.
  3. The key applies to all shipments, not to one shipment. In my run, `POST /shipments/2/refund` with the key of a shipment 1 refund returned 201 and the shipment 1 refund. Shipment 2 got no refund, but the caller sees success.
  4. The key column has no index, so each POST reads all rows of `refunds`.
- **Evidence**: The handler line is `idempotency_key = request.headers.get("Idempotency-Key") or str(uuid.uuid4())`. The schema line is `idempotency_key TEXT NOT NULL,` with no `UNIQUE`. The only new index is `refunds_shipment ON refunds (shipment_id)` (schema.sql:32). Two POSTs without the header returned two different refund ids, 3 and 4.
- **Suggested fix**: Make the header mandatory and return 422 when it is absent. Add `UNIQUE (shipment_id, idempotency_key)`. Do the lookup and the insert in one `db.transaction()`, which uses `BEGIN IMMEDIATE`. Return 409 when a key exists for a different shipment or amount.
- **Effort**: Small (< 1 hour)

### [Medium] The refund writes are not in one transaction
- **Category**: Best practices & idioms
- **Location**: parcelpost/services/refunds.py:36-40 (head cc3d8f2)
- **Problem**: The service inserts the refund, sets the status, and adds the event in three separate statements, and each statement commits alone. If a statement fails after the insert, the refund row exists without the `refunded` status or event. A retry with the same key then returns the stored refund and does not repair the status.
- **Evidence**: `db.connect` sets `isolation_level=None`, so each statement commits alone (parcelpost/db.py:12-13). The sibling service puts the row and its event in one transaction at parcelpost/services/shipments.py:24-30, with the comment "The row and its first event commit together". README.md:10 says "Services own transactions".
- **Suggested fix**: Put the key lookup, the limit check, the insert, `set_status`, and `add_event` in one `with db.transaction():` block. Keep the carrier call outside the transaction, as `services/shipments.py` does.
- **Effort**: Small (< 1 hour)

### [Medium] The refund tests mock every collaborator and do not test any failure path
- **Category**: Testing
- **Location**: tests/test_refunds.py:9-44 (head cc3d8f2)
- **Problem**: The two service tests patch both repos and the carrier client, and then check only the mock calls. No test goes through `dispatch`. So no test checks the status codes, the error shape, the response keys, the SQL, or the schema. These cases have no test:
  - a validation error
  - a missing shipment
  - a void failure
  - a second refund
  - the `status` filter
  - a decimal amount

  One assertion tests nothing: `assert MagicMock() is not None` (line 44).
- **Evidence**: pytest is not installed in this environment, so I ran the test functions with a small runner. All 8 test functions in tests/test_refunds.py and tests/test_shipments.py pass on the head code, but every defect in this report is present. The repo already has the fixtures for real tests: an in-memory database and a `FakeCarrier` with `void_label` (tests/conftest.py:7-33). The sibling tests call `dispatch` (tests/test_shipments.py:5-8, 20-23).
- **Suggested fix**: Replace the mock tests with `dispatch` tests that use the conftest fixtures. Cover these cases:
  - a full refund
  - a partial refund of `"19.99"`
  - a float amount → 422
  - an amount above the charge → 422
  - a missing shipment → 404
  - a second refund → 409
  - a key replay
  - a void failure
  - a `status` value that contains a quote

  Delete line 44.
- **Effort**: Medium (hours)

### [Low] The diff adds dead code and comments that repeat the code
- **Category**: Simplicity & economy
- **Location**: parcelpost/services/refunds.py:13-15, 19-42, 52; parcelpost/repos/refunds.py:4, 25-31; parcelpost/services/refund_strategy.py:7, 33 (head cc3d8f2)
- **Problem**: These lines give the reader more text but no new information or behavior. The sibling modules do not have this kind of text.
- **Evidence**:
  - A banner comment `# Refund service` between two `# ====` lines (services/refunds.py:13-15).
  - Comments that repeat the next line: `# Get the shipment`, `# Insert the refund`, `# Void the label with the carrier` (services/refunds.py:20-42).
  - Docstrings that repeat the function name: `"""Refund a shipment."""`, `"""Look up a refund strategy by name."""`.
  - The unused loop variable `attempt` (services/refunds.py:52).
  - `list_for_shipment` and `SORT_COLUMNS` have no caller (repos/refunds.py:4, 25-31).

  For comparison, the comments at parcelpost/services/shipments.py:20-25 give a reason, for example "create_label is not idempotent at the carrier".
- **Suggested fix**: Delete the banner, the comments that repeat the code, the trivial docstrings, and the repo function that has no caller. Keep the comments that give a reason.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Security**: Other than the SQL injection, I found no hardcoded secrets, no new third-party dependencies, and no new deserialization path. The two new routes are new attack surface. The service has no authn or authz layer, before or after the PR (see Open Questions).
- **Performance**: I found no N+1 queries. The only performance gaps are the absent index on `idempotency_key` and the list without a limit, which are in the findings above.
- **Observability**: I have no separate finding. The two gaps are in the findings above: no log line for a void failure, and no log line for errors that the handler catches. The `log.info` line at services/refunds.py:45 matches the sibling at services/shipments.py:32.
- **Maintainability**: The PR adds no circular imports. The new files follow the existing `handlers/`, `services/`, and `repos/` layout.
- **Code that fits the codebase**:
  - `CarrierClient.void_label` (parcelpost/clients/carrier.py:35-36) matches its siblings at carrier.py:27-33.
  - `shipments_repo.set_status` (parcelpost/repos/shipments.py:41-42) binds its parameters.
  - The `refunds` table has a foreign key and an index on `shipment_id`.
  - The README has rows for the two new routes.

## Open Questions
1. **Must a partial refund void the label?** The service voids the label and sets the status to `refunded` for every refund, partial or full (services/refunds.py:39-43). A domain owner must say if a partial refund, for example for damage, must keep the label valid.
2. **Can a shipment get a refund after delivery?** The other part of this question: what does the carrier do with a void call for a used label, or for a label that is already void? Today a shipment has only the statuses `labelled` and `refunded`. The carrier API documentation for `labels/void` will answer this.
3. **Does the HTTP adapter give header names in a fixed case?** `request.headers.get("Idempotency-Key")` is a case-sensitive dict lookup, and HTTP/2 sends header names in lowercase. If the adapter does not normalize the names, every request gets a random key and has no idempotency. The repo does not contain the adapter, so the adapter source will answer this.
4. **Who can call these routes?** The repo has no authn or authz. The answer sets the exposure of the SQL injection and of the refund endpoint, which moves money. The deployment topology (internal network or public gateway) will answer this.
5. **Did the author see a real pagination bug?** A request log, or a reproduction with the original `id > ?` code, will answer this. The code on `main` does not skip the row at the cursor, because the cursor is the last id that the previous page returned.