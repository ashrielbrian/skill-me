# PR Independent Review

Review of **acme/parcelpost#3, "Bulk shipment creation and uniform carrier retries"**. I did not read the existing review comments.

## PR
acme/parcelpost#3 — Bulk shipment creation and uniform carrier retries
https://github.com/acme/parcelpost/pull/3
7 files changed, +153/-7 lines, head f93e98f

## What this PR does (from the diff)
The PR adds a `POST /shipments/bulk` handler. The handler validates a list of items and calls a new service function. For each item, the service gets a rate through `with_retry`, buys a label, and inserts a row, all inside one database transaction. Then the service reads the rows back with raw SQL. The PR also puts a retry loop into `CarrierClient._post`. The loop retries every `URLError` with linear backoff. Two new settings (`CARRIER_RETRIES`, `CARRIER_BACKOFF_S`) control it. The PR adds tests for both parts.

## What the author says it does
The author says that `POST /shipments/bulk` creates many shipments for one customer and uses "the same validation" as `POST /shipments`. The author says that the batch is all-or-nothing because one transaction writes the rows. The author also says that retries now live in `CarrierClient._post`, so "every carrier call gets them", and that there is "No change to single shipment creation."

## The underlying problem
Customers need to create many shipments in one request and get either all of them or none of them. The PR also names a second problem: a carrier call does not survive a network error unless its call site uses `with_retry`. At base, that problem does not exist:
- Both idempotent call sites already use `with_retry` (`services/quotes.py:8`, `services/shipments.py:19`).
- The only call without retries is `create_label`. `services/shipments.py:20-21` says that this is deliberate.

**Minimal sketch:**
- The handler reuses the single-item parse for each item.
- The service gets all rates and buys all labels before one short transaction.
- The transaction writes each row and its `labelled` event.
- The service voids the bought labels if a step fails.
- A repo function reads the rows back.

The PR is about the same size as this sketch. It leaves out the void step and the event. It also adds a second retry layer that the sketch does not need (see the Architectural fit finding about the second retry policy).

## Summary
The implementation does not match the intent:
- **Most important finding:** the new loop in `_post` also retries `create_label`. A timeout can now buy a second label for every single shipment and every bulk item.
- **All-or-nothing:** the bulk contract holds only for database rows. When a batch fails, the carrier keeps the labels that the service already bought, and the service loses their IDs.
- **Lock:** the bulk transaction holds the SQLite write lock during carrier network calls.

All 24 tests pass at head f93e98f. No test covers these failures.

## Findings

### [Critical] `_post` now retries `create_label`, which can buy duplicate labels
- **Category**: Correctness
- **Location**: parcelpost/clients/carrier.py:25-32 (head f93e98f)
- **Problem**: The retry loop in `_post` applies to every carrier call, including `create_label`. `create_label` is not idempotent at the carrier.
  - If the first attempt times out after the carrier makes the label, the second attempt buys a second label.
  - The service never gets the ID of the first label, so it cannot void it.
  - This affects every `POST /shipments` request and every item in `POST /shipments/bulk`.

  The loop also retries `HTTPError`, because `HTTPError` is a subclass of `URLError`. A 5xx response that the carrier sends after it makes the label also causes a second purchase. A 4xx response gets 3 attempts that cannot succeed.
- **Evidence**:
  - The base code deliberately does not retry this call. At `services/shipments.py:20-22`: "# Not retried: create_label is not idempotent at the carrier, and a retry / # after a timeout can buy a second label." The PR does not change this comment, so the comment is now false.
  - Probe at head: with `retries=3`, a `URLError("timed out")` followed by a success returns `LBL-2` after 2 calls to `POST /labels`.
  - Probe at head: an `HTTPError` 400 on `create_label` makes 3 calls and raises "carrier call labels failed after 3 attempts: Bad Request".
  - The test suite cannot see this. `tests/conftest.py:30-33` replaces the client with `FakeCarrier`, so no service test runs the real `_post`.
- **Suggested fix**: Delete the retry loop from `_post`. Keep retries at the idempotent call sites through `with_retry`. Before you add label retries, add a carrier idempotency key and make the retry opt-in for each call.
- **Effort**: Small (< 1 hour)

### [High] The PR adds a second retry policy, and it stacks with `with_retry`
- **Category**: Architectural fit
- **Location**: parcelpost/clients/carrier.py:24-35; parcelpost/config.py:10-11 (head f93e98f)
- **Problem**: The repo already has one retry policy, `with_retry`. It uses exponential backoff, full jitter, and an injectable `sleep`. The PR adds a second policy with linear backoff and no jitter, and it keeps every `with_retry` call site. The new bulk code adds one more at `services/shipments.py:42-44`.
  - Each `get_rate` now makes up to 9 HTTP calls (3 × 3) before it fails. This affects `POST /quotes`, `POST /shipments`, and `POST /shipments/bulk`.
  - The error message says "after 3 attempts" when 9 calls occurred.
  - Without jitter, clients that fail at the same time also retry at the same time.
  - Operators now must tune two separate settings: the env vars and the `with_retry` defaults in code.
- **Evidence**:
  - `README.md:15`: "`parcelpost/retry.py` | `with_retry`, the one retry policy for carrier calls." The PR does not update this line, so it is now false.
  - The existing policy is at `retry.py:10-30`. The existing call sites are at `services/quotes.py:8` and `services/shipments.py:19`.
  - Probe at head: `with_retry(lambda: c.get_rate(...))` with the carrier down makes 9 HTTP calls and raises "carrier call rates failed after 3 attempts: down".
- **Suggested fix**: Delete the loop in `_post`, the `time` import, and the two new settings. If you need retry settings, feed them into `with_retry` in `retry.py`.
- **Effort**: Small (< 1 hour)

### [High] The bulk transaction holds the SQLite write lock during carrier network calls
- **Category**: Architectural fit
- **Location**: parcelpost/services/shipments.py:40-45 (head f93e98f)
- **Problem**: `db.transaction()` runs `BEGIN IMMEDIATE` (`db.py:39`), which takes the write lock. The PR then makes 2 carrier calls for each item while it holds the lock.
  - The batch size has no limit, so the time that the lock is held also has no limit.
  - **Worst case per item:** with the defaults (`CARRIER_TIMEOUT_S` 5, `CARRIER_RETRIES` 3, `CARRIER_BACKOFF_S` 0.5, `with_retry` 3 attempts), one item can hold the lock for up to about 66.6 seconds.
  - **Normal case (example):** at 200 ms for each carrier call, a 50-item batch holds the lock for about 20 seconds.

  All threads in the process share one connection (`db.py:13`, `check_same_thread=False`). Another thread's write fails at `BEGIN IMMEDIATE`. A write from another process waits for the sqlite3 busy timeout (default 5 seconds), then fails with "database is locked".
- **Evidence**:
  - The sibling path does the opposite on purpose. `services/shipments.py:18-22` calls the carrier first. `services/shipments.py:26-30` keeps only the two inserts in the transaction.
  - Probe at head: while a bulk request waits in `create_label`, a concurrent `POST /shipments` returns 500 with `sqlite3.OperationalError: cannot start a transaction within a transaction`. That request already bought its label at `services/shipments.py:22`, so the service also loses that label.
  - How I got 66.6 s:
    - `get_rate`: 9 calls × 5 s, plus 3 × (0.5 + 1.0) s of `_post` sleeps, plus up to 0.6 s of `with_retry` sleeps, is 50.1 s.
    - `create_label`: 3 × 5 s + 1.5 s is 16.5 s.
- **Suggested fix**: Move all carrier calls out of the transaction, as `create_shipment` does. Open the transaction only to write rows and events. Set a maximum number of items.
- **Effort**: Small (< 1 hour), together with the fix for the label-loss finding

### [High] The bulk service copies `create_shipment` and already differs from it
- **Category**: Architectural fit
- **Location**: parcelpost/services/shipments.py:36-62; parcelpost/handlers/shipments.py:27-37 (head f93e98f)
- **Problem**: The PR copies the single-item logic into the handler and the service instead of reusing it. The copy already differs in three ways:
  1. **No `labelled` event.** `services/shipments.py:24-25` states the rule: "a shipment never exists without its 'labelled' event." Bulk rows break this rule. A later fix needs a backfill.
  2. **Wrong default for `declared_value`.** When the field is absent, bulk stores `0` (`handlers/shipments.py:35`, `item.get("declared_value", 0)`). The single path stores `NULL` (`handlers/shipments.py:17`), and the schema allows `NULL` (`schema.sql:9`). Bulk records "declared 0.00" where the single path records "not declared".
  3. **Raw SQL in a service.** The service runs SQL directly (`services/shipments.py:59-62`). `README.md:11` says "Only repos call `parcelpost.db`." The precedent is `repos/shipments.py:5-16`.
- **Evidence**: Probe at head, with one bulk shipment and one single shipment:
  - `events per shipment [{'id': 1, 'n': 0}, {'id': 2, 'n': 1}]`
  - `declared_cents [{'id': 1, 'declared_cents': 0}, {'id': 2, 'declared_cents': None}]`
- **Suggested fix**:
  - Move the parse in the single handler into a helper, and call it for each item.
  - Let both paths share the insert-and-event step.
  - Add `shipments_repo.get_many(ids)` for the read-back.
- **Effort**: Small (< 1 hour)

### [High] "All-or-nothing" covers only database rows, and the service loses the labels it bought
- **Category**: Correctness
- **Location**: parcelpost/services/shipments.py:40-58 (head f93e98f)
- **Problem**: The service buys a label for each item inside the transaction. If item k fails:
  - The rollback removes rows 1 to k-1.
  - The carrier keeps labels 1 to k-1.
  - The service does not void those labels, store their IDs, or log them. The log line at line 58 runs only on success.

  So each failed batch loses paid labels, and no record of them exists. Any carrier rejection can cause this. For example, the handler does not check `carrier`, so an unknown carrier name fails only at the carrier.
- **Evidence**:
  - Probe at head: items `[ITEM, ITEM, {weight_g: 999}]`, where `get_rate` fails for 999, return 502. Two labels are bought and 0 rows are saved.
  - `test_bulk_is_all_or_nothing` (`tests/test_bulk.py:29-41`) checks only for an empty table.
  - The test fake already tracks voided labels (`tests/conftest.py:10`, `self.voided = []`; `tests/conftest.py:19-20`, `void_label`). The real `CarrierClient` has no `void_label`.
- **Suggested fix**:
  - Get all rates first. Then buy the labels and record each label ID.
  - If a step fails, void each bought label. Log each void that fails.
  - Write the rows and events in one short transaction after the carrier calls.
  - If the carrier cannot void labels, drop the all-or-nothing contract and return a result for each item.
- **Effort**: Medium (hours)

### [Medium] The PR bundles two unrelated changes
- **Category**: Scope & PR hygiene
- **Location**: PR-wide
- **Problem**: The bulk endpoint only adds behavior. The retry change alters every carrier call in production. In one PR, you cannot review, release, or revert the two changes separately. A revert of the retry change also removes the bulk endpoint.
- **Evidence**: `clients/carrier.py` and `config.py` do not depend on the bulk code. The bulk code does not depend on them.
- **Suggested fix**: Split the PR into two PRs. Because of the findings above, drop the retry PR or rewrite it.
- **Effort**: Small (< 1 hour)

### [Medium] `CARRIER_RETRIES` counts attempts, and the value 0 breaks every carrier call
- **Category**: API & interface design
- **Location**: parcelpost/clients/carrier.py:11, 25, 33-35; parcelpost/config.py:10 (head f93e98f)
- **Problem**: The setting holds the total number of attempts, not the number of retries.
  - `CARRIER_RETRIES=3` gives 2 retries.
  - The constructor default `retries=1` gives no retry. The settings default `3` gives 2 retries.
  - An operator who sets `CARRIER_RETRIES=0` to turn retries off gets zero attempts. `last` stays `None`, line 34 raises `AttributeError`, and every carrier call returns 500.

  The existing helper uses the clear name `attempts` (`retry.py:13`).
- **Evidence**: Probe at head: `CarrierClient(..., retries=0).get_rate(...)` raises `AttributeError 'NoneType' object has no attribute 'reason'`.
- **Suggested fix**: If the setting stays, rename it to `CARRIER_ATTEMPTS`. Reject values below 1 at startup.
- **Effort**: Small (< 1 hour)

### [Medium] The PR description makes four claims that the diff does not support
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: A reviewer who trusts the description will miss the Critical finding. The four claims are:
  1. "No change to single shipment creation." Single creation now retries `create_label` and makes up to 9 `get_rate` calls. `POST /quotes` also changes.
  2. "goes through the same validation". The `declared_value` default differs (`handlers/shipments.py:35` vs. `handlers/shipments.py:17`).
  3. "if any item fails, nothing is saved". The labels stay bought at the carrier.
  4. "A carrier call only survived a transient network error if its call site remembered to wrap it in `with_retry`." At base, every idempotent call site uses `with_retry`. The only call without it, `create_label`, has none on purpose (`services/shipments.py:20-21`).
- **Evidence**: See the lines cited above. The probes in the Critical finding and the label-loss finding show claims 1 and 3.
- **Suggested fix**: Correct the description after you change the code. State the contract for what happens at the carrier when a batch fails.
- **Effort**: Small (< 1 hour)

### [Medium] The handler does not check the shape or size of `items`
- **Category**: Security
- **Location**: parcelpost/handlers/shipments.py:25-29 (head f93e98f)
- **Problem**: The new endpoint is new attack surface. The handler does not check that `items` is a list of objects, and it does not limit its length.
  - **Bad shapes:** a value that is not a list, or a list with a non-object, returns 500 `internal`, not 422. In a probe, `"abc"`, `5`, a single object, and `[None]` all return 500.
  - **Empty list:** `[]` returns 201 with `{"items": []}` and runs an empty `BEGIN IMMEDIATE`/`COMMIT`.
  - **No size limit:** one request with N items buys N labels and holds the write lock N times as long as one item does.
  - **Too many items:** above the SQLite limit on bound parameters (999 before SQLite 3.32.0, 32766 after), the read-back at `services/shipments.py:60` fails after the commit. The client gets 500 for a saved batch, and a retry from the client makes duplicates.
  - **Unclear errors:** an item error does not give the item index.
- **Evidence**: `require_fields` calls `body.get` (`validation.py:7`), which fails on values that are not dicts. `http.py:54-58` turns the `AttributeError`/`TypeError` into a 500.
- **Suggested fix**: Accept only a non-empty list of dicts. Set a maximum count, for example 100. Add the `items[i]` index to each item error.
- **Effort**: Small (< 1 hour)

### [Medium] The tests do not cover the new failure modes
- **Category**: Testing
- **Location**: tests/test_bulk.py:23-41; tests/test_carrier_client.py:26-38 (head f93e98f)
- **Problem**: The tests miss these cases:
  - `test_bulk_is_all_or_nothing` checks the table but not the carrier. It passes while 2 labels are lost.
  - `test_bulk_validates_each_item` fails in the handler before the service runs, so it does not test the rollback.
  - No test checks that `create_label` makes exactly one HTTP call. Such a test fails at head.
  - No test covers events for bulk rows, an absent `declared_value`, a malformed `items`, or `retries=0`.
- **Evidence**: `tests/conftest.py:30-33` replaces the client in every test, so no service test runs the real `_post`.
- **Suggested fix**:
  - Assert `fake_carrier.voided` in the all-or-nothing test.
  - Add a client test that checks for one `create_label` call after a timeout.
  - Add one test for each difference in the copy finding.
- **Effort**: Small (< 1 hour)

### [Low] The bulk log line leaves out the shipment and label IDs
- **Category**: Observability
- **Location**: parcelpost/services/shipments.py:58 (head f93e98f)
- **Problem**: The single path logs the shipment ID and the label ID (`services/shipments.py:32`). The bulk path logs only a count. When a batch fails, the service logs nothing about the labels it bought. Without the IDs, an operator cannot match carrier invoices to batches.
- **Evidence**: `log.info("%s shipments created for customer %s", len(shipment_ids), customer_id)`
- **Suggested fix**: Log each shipment ID and label ID. If a batch fails, log each bought label ID at `warning` or higher.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **SQL injection:** none. The f-string at `services/shipments.py:61` inserts only `?` placeholders, and the values are bound.
- **Closure bug:** none. The lambda at `services/shipments.py:43` runs inside the same loop step, so `item` is correct.
- **Error format:** new errors go through `AppError` and keep the documented error shape.
- **Sensitive logging:** the changed files add no sensitive data to the logs.
- **Concurrency idioms:** the code is synchronous and has no async misuse.
- **Read performance:** there is no N+1 pattern. The read-back is one query.
- **Maintainability:** the PR adds no circular imports.
- **Tests:** all 24 tests pass at head f93e98f. I ran them in a temporary worktree, which I then removed.

## Open Questions
1. **Threads or processes?** Does the deployment serve requests on many threads or on many processes? The repo has no server code. `check_same_thread=False` (`db.py:13`) suggests threads. The answer changes only how the lock failure shows. It fails either way.
2. **Void and idempotency support:** Can the carrier API void a label, and for how long after purchase? Does it accept an idempotency key? `FakeCarrier.void_label` (`tests/conftest.py:19`) suggests that void exists, but `CarrierClient` has no such method. The answer decides between a void step and results for each item.
3. **When does the carrier charge?** Does the carrier charge for a label at purchase or at first scan? The answer sets the cost of each lost or duplicate label.
4. **Who can call the endpoint?** The repo has no authentication or authorization layer. The answer sets the severity of the unlimited `items` finding.
5. **Why the retry change?** What incident led to it? At base, only `create_label` has no retries, and that is deliberate. An incident report would show whether a real gap exists elsewhere, for example read timeouts in `response.read()`, which `_post` does not catch.