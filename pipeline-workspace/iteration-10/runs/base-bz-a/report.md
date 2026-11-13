# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout. It also adds a gateway route that sends the request to checkout with no changes.

- The new `refund_order` function locks the order row and computes an amount from `items_json`.
- It inserts a `refunds` row and sets the status to `refunded`.
- For a settled order, it also writes a `payout_adjustments` row.
- After the commit, it publishes a stream event and sends an `order.refunded` webhook.

Migration 0008 creates the `refunds` table and an index on `orders (status, updated_at)`. The state machine now lets `placed` and `settled` orders move to `refunded`.

## What the author says it does
The author says that the PR adds full-order refunds for placed and settled orders and gives clients access to them through the gateway. The author makes these claims:

- The refund is the full charge.
- A row lock stops a double refund.
- The next settlement run corrects the seller payout.
- The event and the webhook let consumers "see refunds the same way they see placements and cancellations".
- The new index is "for the refunds dashboard".

## The underlying problem
Operators or buyers need to give back the full charge for an order. When the seller already has the money, the platform must also take that amount back from the seller.

The smallest correct change does these steps:

- Read the stored `total_cents` under a row lock.
- Insert a `refunds` row and set the status.
- Register the new webhook topic.
- Let settlement build the seller clawback from its own rows.

The diff is about the same size as that change. But it adds three parts that the minimal change does not need: an `orders` index, the first stream publish from checkout, and direct writes into settlement tables.

## Summary
The PR puts the refund flow in the correct modules, but the money paths do not do what the description says. The most important finding is security: any signed-in caller can refund any order through the gateway. The gateway does no scope check, no ownership check, and writes no audit record.

There are three more large defects:

- The refund amount is different from the charge.
- Settlement ignores the seller clawback, and the clawback has the wrong sign.
- The new webhook topic cannot get subscribers.

## Findings

### [Critical] Any signed-in caller can refund any order
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55-60 (head f61de6b); services/gateway/routes.py:24; services/checkout/routes.py:68-76; services/checkout/orders/refund.py:15
- **Problem**: The new gateway handler sends each refund to checkout with no scope check and no ownership check. The auth middleware only makes sure that a bearer token is present (services/gateway/middleware/auth.py:36-41). Checkout gets only the order ID and the reason. It does not get the user ID or the tenant ID, so it cannot check ownership.

  Thus any session of any tenant can refund any order whose ID it knows. Order IDs are not secrets: checkout sends them to third-party webhook URLs (services/checkout/orders/create.py:96-104). A buyer can also refund their own order after settlement, with no approval step. The refund moves money, and after the clawback fix it also takes money from a seller.
- **Evidence**:
  - `resp = await _client.post("/v1/orders/" + order_id + "/refund", json=body)` (orders_proxy.py:59). The handler does not read `session`.
  - The pattern for a guarded write: `require_scope(request, "tenant:read")` (services/gateway/handlers/tenants.py:26). The docstring of `require_scope` says "Called by handlers that guard writes." (auth.py:25).
  - The pattern for ownership: `headers={"x-tenant-id": session.tenant_id}` (orders_proxy.py:24) and `params={"user_id": session.user_id}` (orders_proxy.py:38).
  - The pattern for an audit record on an order write: `await write_audit(ctx, actor_id=session.user_id, action="order.place")` (orders_proxy.py:28-29). The refund handler writes no audit record.
  - Checkout has `POST /v1/orders/{order_id}/cancel` (services/checkout/routes.py:25), but the gateway route table does not include it (services/gateway/routes.py:21-24). The PR gives clients access to refund, which moves more money than cancel.
  - `_LOCK_ORDER` selects `buyer_id` (refund.py:15), but no code reads it.
- **Suggested fix**: First, decide who may refund: the buyer, support staff, or the seller. Until that decision, delete the gateway route and keep refund internal, like cancel. If clients must call it, call `require_scope` with a new refund scope in the handler. Send `session.user_id` and `session.tenant_id` to checkout. In `refund_order`, compare them with the order owner and reject a mismatch. Write an audit record for each refund.
- **Effort**: Medium (hours)

### [Critical] Settled-order refunds never reduce the seller payout
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:22-26, 56-60 (head f61de6b)
- **Problem**: For a settled order, the PR writes a `payout_adjustments` row. Settlement ignores that row, so the seller keeps the full payout for a refunded order. The only sign is a lower adjustment count in the settlement log line (services/settlement/cron.py:66-71). There are four defects:
  1. Reason: the row has reason `'refund'`. `VALID_REASONS` does not contain `'refund'`. `validate_adjustment` returns False, and `merge_adjustments` skips the row and logs nothing (services/settlement/payouts/adjustments.py:7-9, 14-15, 29-30).
  2. Sign: the row has a positive `amount_cents`, and `merge_adjustments` adds it to `gross_cents` (adjustments.py:34). If someone changes only the reason to a valid value, settlement pays the seller the refund amount a second time.
  3. Unmatched seller: `merge_adjustments` skips a row when the seller has no settled orders on that day (adjustments.py:31-33). Settlement loses the clawback for a seller with no sales on the refund day.
  4. One seller: `_SELLER_QUERY` uses `fetchrow`, which returns one row from an unordered result (refund.py:22, 59). If `settled_orders` has more rows for the order, one seller gets the full charge. If it has no row, `seller["seller_id"]` raises `TypeError` and the request returns 500.

  Also, the clawback amount is the buyer refund with tax. It is not the amount that the seller got. `settled_orders` has `gross_cents` and `net_cents` for each row (cron.py:29-32).
- **Evidence**: `VALUES ($1, $2, 'refund', CURRENT_DATE)` (refund.py:25). `VALID_REASONS = frozenset(` `{"refund_correction", "chargeback", "goodwill", "fee_waiver"}` (adjustments.py:7-9). `entry["gross_cents"] += adjustment["amount_cents"]` (adjustments.py:34).
- **Suggested fix**: Move the clawback into settlement (see the next finding). In that code:
  - Use a reason from `VALID_REASONS`, or add `refund` to it in the same PR.
  - Write a negative amount.
  - Take the amount from each `settled_orders` row for the order.
  - Carry forward an adjustment when the seller has no sales on that day.

  Add a test that sends the clawback row through `merge_adjustments` and asserts that the seller total goes down.
- **Effort**: Medium (hours)

### [High] Checkout reads and writes settlement-owned tables
- **Category**: Architectural fit
- **Location**: services/checkout/orders/refund.py:22-26, 56-60 (head f61de6b)
- **Problem**: Before this PR, no checkout code used `settled_orders` or `payout_adjustments`. Only settlement (services/settlement/cron.py:29-37) and an ops tool (tools/verify_ledger.py:22-26) read them. The PR adds a new dependency from checkout to settlement data. Checkout now must know the settlement reason codes, sign rule, and day rule. The previous finding shows that it gets all three wrong. Each later change to the settlement adjustment rules must also change checkout.
- **Evidence**:
  - Checkout calls a sibling service through an HTTP client, not through its tables: `release_order` posts to inventory (services/checkout/clients/inventory_client.py:26-33).
  - Data already moves in the other direction. `items_json` "is written at order placement and read by the settlement export" (services/checkout/orders/models.py:3-5).
  - Settlement loads its own inputs (cron.py:43-48). Its adjustments module says "Adjustment rows come from the support tooling table" (services/settlement/payouts/adjustments.py:3-4).
- **Suggested fix**: Let settlement own the clawback. Checkout records the refund in `refunds`. Settlement reads the refunds of the day, or consumes the refund event, and builds negative adjustments from its own `settled_orders` rows. Delete `_SELLER_QUERY`, `_INSERT_ADJUSTMENT`, and the settled branch from checkout.
- **Effort**: Medium (hours)

### [High] The migration locks `orders` to build an index that no code reads
- **Category**: Simplicity & economy
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-24, 31-33 (head f61de6b)
- **Problem**: The migration builds `orders_status_updated_idx` on `orders (status, updated_at)`. No production query in the repo or in the PR filters or sorts `orders` on these columns. The PR body says that the index is "for the refunds dashboard". But a refunds dashboard reads `refunds`, and no dashboard code exists.

  The migration builds the index with a plain `CREATE INDEX` inside a transaction. This takes a SHARE lock that blocks every INSERT and UPDATE on `orders` until the commit. Checkout writes about 800 orders per minute, and the peak is 13 per second (services/checkout/README.md:7; services/checkout/routes.py:38). Settlement reads about 1.2M order rows per day (services/settlement/README.md:3), so `orders` is large and the build can take minutes. During the build, placement, cancel, and refund all wait. After the build, each order insert and each status update also writes to the new index.
- **Evidence**:
  - The production reads of `orders` all filter on `id` only: services/checkout/orders/summary.py:8, services/checkout/orders/create.py:29, refund.py:15.
  - The precedent migration creates an index only on its own new table (services/checkout/migrations/0006_add_webhooks.py:22-25).
- **Suggested fix**: Delete `_CREATE_STATUS_INDEX`. If a real query needs the index later, add it in a separate migration. Use `CREATE INDEX CONCURRENTLY` outside a transaction. While you change this migration, add `UNIQUE (order_id)` to `refunds`. A full-order refund occurs at most once for each order. The constraint makes the database enforce that limit and gives the foreign key an index.
- **Effort**: Small (< 1 hour)

### [High] The refund amount is not the amount that the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35, 52 (head f61de6b)
- **Problem**: `refund_amount_cents` computes a new total from `items_json`. That total does not match the charge from `grand_total_cents` and `apply_promo` (services/checkout/orders/create.py:75-80). There are three differences:
  1. Shipping: below a 7,500-cent subtotal, the charge adds 599 to 1,299 cents of shipping (services/checkout/carts/totals.py:10-19, 35-39). The refund does not include it.
  2. Promo: the charge subtracts the promo discount (services/checkout/promotions.py:31-33). The refund ignores the discount, so a promo order gets back more than the buyer paid.
  3. Rounding: the charge rounds the tax of each line with `round` (totals.py:27-32). The refund truncates `line * (1 + rate)` with `int`. I tested unit prices from 100 to 19,999 cents (step 37) against every rate that `libs/commerce/tax.py` can make. The result: a 1-cent under-refund in 17,354 of 34,970 single-line cases (49.6 percent). Example: 137 cents at rate 0.02 gives a charge of 140 and a refund of 139.

  With the PR test data and region `us`, the charge is 4,539 cents and the refund is 3,940 cents (599 cents short). With a 20 percent promo, the charge is 3,631 cents and the refund is 3,940 cents (309 cents too much).
- **Evidence**: `orders` already stores the charged amount in `total_cents` (services/checkout/orders/txn.py:6-10; create.py:44-45). `_LOCK_ORDER` does not select it (refund.py:15). The new function is a second copy of the money math in `services/checkout/carts/totals.py:42-45`, and it rounds in a different way.
- **Suggested fix**: Select `total_cents` in `_LOCK_ORDER` and refund that value. Delete `refund_amount_cents`. Then the code no longer loads `items_json` (about 25 KB for each order, services/checkout/orders/models.py:3-4) while it holds the row lock.
- **Effort**: Small (< 1 hour)

### [High] No integrator can subscribe to `order.refunded`
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:69 (head f61de6b); services/checkout/webhooks/registry.py:8-15
- **Problem**: `dispatch("order.refunded", event)` sends the event to the subscribers of that topic. `add_subscription` rejects each topic that is not in `KNOWN_TOPICS`, and the PR does not add `order.refunded` to that set. Thus no subscription to the topic can exist, and `dispatch` always sends to zero URLs. Integrators do not see refunds, although the PR body says that they do.
- **Evidence**: `KNOWN_TOPICS` contains only `"order.placed"`, `"order.reserved"`, `"order.settled"`, `"order.cancelled"` (registry.py:8-15). `if topic not in KNOWN_TOPICS: raise ValidationFailure(["unknown topic %s" % topic])` (registry.py:36-37).
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that `add_subscription("order.refunded", ...)` succeeds.
- **Effort**: Small (< 1 hour)

### [Medium] The gateway refund handler does not use the edge response contract
- **Category**: Architectural fit
- **Location**: services/gateway/handlers/orders_proxy.py:7, 55-60 (head f61de6b)
- **Problem**: The new handler returns `web.json_response(resp.json(), status=resp.status_code)`. All sibling handlers return through `responses.envelope` or `responses.failure`. This has three effects:
  1. The response has no security headers, for example `strict-transport-security` and `cache-control: no-store`. It also has no `{"status", "data"}` envelope (services/gateway/responses.py:10-27). A client that reads `data` fails on this one route.
  2. The handler does not change a checkout 5xx into a 502 `upstream_error`. It sends the raw checkout error body to the client.
  3. When checkout fails with an unhandled exception, aiohttp returns a plain-text 500. Then `resp.json()` raises in the gateway, and the gateway also returns 500.
- **Evidence**: responses.py:3-4 says: "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints." The siblings follow this rule at orders_proxy.py:26-30, 39-41, and 48-52.
- **Suggested fix**: Use the same structure as `get_order`. Send a 404 and a 5xx through `responses.failure`. Return `responses.envelope(resp.json(), status=resp.status_code)`. Delete the `from aiohttp import web` import.
- **Effort**: Small (< 1 hour)

### [Medium] The stream event stays in a buffer that checkout never flushes
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:68 (head f61de6b)
- **Problem**: This is the first `publish` call in checkout. `publish` only adds the event to an in-process buffer. The buffer goes to Redis only when it holds 200 events, or when the producer calls `flush` (libs/eventkit/publisher.py:28-34; libs/eventkit/config.py:10). Checkout never calls `flush`, and its cleanup hook does not flush (services/checkout/app.py:30-34). At 3 refunds per minute, the 200th event comes after about 67 minutes. A deploy or a crash before then loses every buffered event. There are also these effects:
  - `flush` uses the sync `redis` client (publisher.py:20-25, 41-50). It blocks the aiohttp event loop during the round trip.
  - If `flush` raises, the refund request that started it returns 500 after the commit. The client sees a failure for a refund that occurred. The webhook dispatch for that refund does not run.
  - `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES` (libs/eventkit/schema.py:28-44).
  - No consumer of this event exists in the repo.
- **Evidence**: Placement and cancel do not publish to a stream. They only call `dispatch` (services/checkout/orders/create.py:52, 71).
- **Suggested fix**: If a service needs the event, call `flush()` after `publish`, and also flush in `_on_cleanup`. Settlement needs it if it builds the clawback from the event. Register `checkout.order_refunded` in `KNOWN_EVENT_TYPES`. If no consumer needs the event now, delete the publish call from this PR.
- **Effort**: Small (< 1 hour)

### [Medium] A concurrent cancel can overwrite a refunded order
- **Category**: Correctness
- **Location**: services/checkout/orders/models.py:47 (head f61de6b); services/checkout/orders/create.py:62-71; services/checkout/orders/txn.py:16
- **Problem**: After this PR, a `placed` order can move to `cancelled` or to `refunded`. Only the refund path locks the row. `cancel_order` reads the status with no lock and then calls inventory. Then it writes `cancelled` with an `UPDATE` that does not check the current status.

  This order of events can occur:
  1. Cancel reads `placed`.
  2. Refund locks the row, writes `refunded`, and commits.
  3. Cancel writes `cancelled`.

  The order then has the status `cancelled` and also a `refunds` row. Both webhooks fire, and inventory releases the holds. The inventory call between the read and the write has a 10-second timeout (services/checkout/config.py:28), so the window is wide. The PR body says that two requests "cannot both refund one order". That is true for two refunds, but not for a refund and a cancel.
- **Evidence**: `_UPDATE_STATUS = "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1"` (txn.py:16). `cancel_order` calls `fetch_one(_STATUS_QUERY, ...)`, then `release_order`, then `update_status(order_id, "cancelled")` (create.py:62-70).
- **Suggested fix**: Make each status write conditional on the expected current status. If the write changes 0 rows, return a conflict. Or lock the row in `cancel_order` in the same way as `refund_order`.
- **Effort**: Small (< 1 hour)

### [Medium] The tests compare the formula with itself and do not run the refund flow
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:25-29 (head f61de6b)
- **Problem**: `test_sums_every_line_with_tax` expects `3240 + 700`, which is the output of the same formula. The test does not compare the refund with `grand_total_cents` or with the stored total, so it passes on data where the refund is 599 cents short. No test runs `refund_order`. So no test covers the lock query, the status check, the `refunds` insert, the settled branch, or the clawback row. No test covers the checkout route, the gateway handler, the webhook topic, or the settlement merge of the new row.
- **Evidence**: The repo has test doubles for this work. `FakeConn` and `FakePool` record each query and its arguments (tests/helpers/fakes.py:11-59). `make_pool` comes from tests/conftest.py, and tests/test_checkout_flow.py:7 and tests/test_settlement_rollup.py:62 use it. I could not run the PR tests here: the import of `libs.dbkit` fails with `ModuleNotFoundError: No module named 'asyncpg'`.
- **Suggested fix**: Add these tests:
  - A test that the refund equals the stored `total_cents` for an order with shipping and a promo.
  - `refund_order` tests with `FakePool` for `placed`, for `settled`, and for a rejected status.
  - A test that sends the clawback row through `merge_adjustments` and asserts that the seller total goes down.
- **Effort**: Medium (hours)

### [Medium] Five claims in the PR description do not match the diff
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: A reviewer who trusts the description will approve money behavior that the code does not have.
- **Evidence**:
  1. "The refund amount is the full amount the buyer was charged." The amount does not include shipping or the promo, and it truncates the tax (refund.py:29-35).
  2. "the seller's payout is corrected in the next settlement run." Settlement skips the row, and the sign is wrong (adjustments.py:7-9, 34).
  3. "downstream services and integrators see refunds the same way they see placements and cancellations." Placement and cancel do not publish to the stream (create.py:52, 71). The `order.refunded` topic cannot get subscribers (registry.py:8-15).
  4. "an index for the refunds dashboard". The index is on `orders`, and no code reads it (0008_add_refunds.py:22-24).
  5. "two concurrent requests cannot both refund one order". This is true, but a concurrent cancel can overwrite the refund (txn.py:16).
- **Suggested fix**: Fix the code in the findings above. Then change the PR body so that each claim matches the code.
- **Effort**: Small (< 1 hour)

### [Low] An empty or non-object request body causes a 500
- **Category**: Error handling
- **Location**: services/gateway/handlers/orders_proxy.py:58; services/checkout/routes.py:71-73 (head f61de6b)
- **Problem**: The code treats `reason` as optional (`payload.get("reason", "")`). But if the POST has no body, `request.json()` raises `JSONDecodeError` in the gateway, and the client gets 500. A JSON body that is not an object, for example `[]`, gets through the gateway. Then it raises `AttributeError` at `payload.get` in checkout. These are client errors, so they must return 400.
- **Evidence**: `payload = await request.json()` is outside the `try` block (routes.py:71). The sibling `create_order` has the same structure (routes.py:39), but this is the first route where the body is optional.
- **Suggested fix**: Read the body with a fallback to `{}`. If the body is not a dict, raise `ValidationFailure`.
- **Effort**: Small (< 1 hour)

### [Low] A status list and the service README do not include the refund
- **Category**: Maintainability
- **Location**: services/checkout/config.py:22; services/checkout/README.md:25-33 (head f61de6b)
- **Problem**: The PR adds a status and an endpoint, but two related lists do not change. `ORDER_STATUS_VALUES` does not contain `refunded`, and the README endpoint list does not contain the refund route. No code reads `ORDER_STATUS_VALUES` now, so nothing fails today. The next person who uses that list will get an incorrect set.
- **Evidence**: `ORDER_STATUS_VALUES = ("pending", "reserved", "placed", "settled", "cancelled")` (config.py:22).
- **Suggested fix**: Add `refunded` to the tuple, or delete the unused tuple. Add the refund route to the README endpoint list.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **API & interface design**: The route shape matches the cancel route (services/checkout/routes.py:25). The 201 status matches `create_order`. The only API defect is the gateway response format, which is in a finding above.
- **Security, other than access control**: All new SQL uses numbered placeholders. The PR has no secrets and adds no third-party dependency.
- **Resource handling**: `transaction()` returns the connection on each path (libs/dbkit/txn.py:18-31). The new migration awaits `get_pool()` correctly.
  - Outside this PR: `get_pool` is `async`, but these older calls do not await it: services/checkout/migrations/0006_add_webhooks.py:39, 0007_split_addresses.py:25, services/checkout/orders/txn.py:32, and services/checkout/app.py:26.
- **Performance**: The refund rate is a few requests per minute. The only performance risk is the index build in the migration, which is in a finding above.
- **Observability**: The refund path writes no log line. `cancel_order` also writes none, so this matches the current practice. The missing audit record is part of the security finding.
- **Scope & PR hygiene**: The PR does one feature and has no unrelated edits. If you remove the index, the migration only adds a table, so you can roll it back easily.

## Open Questions
1. What returns the money to the buyer? The repo has no payment-provider code. If a service outside the repo reads `checkout.order_refunded` to move money, the buffered-publish finding becomes Critical. To resolve: name the consumer and show how it reads the stream.
2. Must a refund of a `placed` order release the inventory holds? `cancel_order` releases them (services/checkout/orders/create.py:69), but `refund_order` does not. To resolve: tell me if a refunded `placed` order still ships.
3. Can one order have more than one seller? If yes, the clawback must split the amount by seller. To resolve: give the schema and the key of `settled_orders`.
4. Which time zone does the checkout database session use? `CURRENT_DATE` (refund.py:25) must give the same UTC day that `previous_utc_day()` gives (services/settlement/cron.py:42). To resolve: give the `timezone` setting for the checkout database role.
5. Does the checkout deployment set `BAZAAR_REDIS_URL`? Checkout config reads `CHECKOUT_REDIS_URL` (services/checkout/config.py:16). eventkit reads `BAZAAR_REDIS_URL`, and its default is `redis://redis.internal.bazaar:6379/0` (libs/eventkit/config.py:15-18). To resolve: give the checkout deploy environment.
6. Which code moves an order to `settled`, and does settlement still pay out an order that moved from `placed` to `refunded`? That code is not in the repo. If it writes the status with no condition, it can also overwrite `refunded`. To resolve: name the writer of `settled` and the source of `settled_orders`.