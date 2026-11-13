# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout. It also adds a gateway route that forwards the call to checkout without changes. The checkout function locks the order row and computes an amount from `items_json`. Then it writes a `refunds` row, sets the status to `refunded`, and inserts a `payout_adjustments` row for a settled order. After the commit, it publishes a stream event and sends a webhook. Migration 0008 adds the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says that the PR adds full-order refunds for placed and settled orders, and that clients can get to the refund route through the gateway. The author makes these claims:
- The refund is "the full amount the buyer was charged".
- A row lock stops two concurrent refunds of one order.
- For a settled order, "the seller's payout is corrected in the next settlement run".
- The PR emits `checkout.order_refunded` and an `order.refunded` webhook, "the same way they see placements and cancellations".
- Migration 0008 adds "an index for the refunds dashboard".

## The underlying problem
A buyer or support agent must be able to return the full charged amount of an order. When the seller already got paid for that order, the marketplace must take that amount back from the seller. The minimal change has these parts: one checkout function next to `cancel_order` that locks the row, refunds the stored `total_cents`, writes a `refunds` row, and calls `dispatch` with a registered topic; one route; and one migration for `refunds`. Settlement then builds its own payout adjustment from the refunds. The PR has about the same number of files as this sketch. But it also adds parts the sketch does not need: a new money formula in place of `total_cents`, an index on `orders` that no query uses, a direct write into a settlement table, and a stream event with no consumer.

## Summary
The status move and the row lock are correct. The `FOR UPDATE` lock does stop a double refund. Most other money paths are wrong, so the code does not do what the description says:
- The buyer gets a wrong amount.
- Settlement silently drops the seller adjustment, and the adjustment has the wrong sign.
- Nobody can subscribe to the webhook.
- The stream event stays in a buffer.

The most important finding is that any client with a valid session token can refund any order through the gateway. No scope check, ownership check, or audit record protects this route.

## Findings

### [Critical] Any authenticated client can refund any order through the gateway
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55 (head f61de6b); services/gateway/routes.py:24; services/checkout/orders/refund.py:15
- **Problem**: The PR puts a new money-moving endpoint on the public edge. The auth middleware checks only that a Bearer token is present (services/gateway/middleware/auth.py:36-41). The handler calls no `require_scope` and sends no caller identity to checkout. Checkout selects `buyer_id` (refund.py:15) but never compares it with the caller. Because of this, a buyer can refund an order that belongs to a different buyer. A buyer can also refund their own settled order, and no policy check stops it. Neither the gateway nor checkout writes an audit record, so no record shows who moved the money.
- **Evidence**:
  - `place_order` checks the session and returns 401 (orders_proxy.py:19-21). It sends `x-tenant-id` (orders_proxy.py:24) and writes an audit record (orders_proxy.py:28-29).
  - The tenant handlers call `require_scope(request, "tenant:read")` (services/gateway/handlers/tenants.py:26). The `require_scope` docstring says: "Called by handlers that guard writes." (auth.py:25)
  - `libs/commerce/audit.py:1`: "Audit records for money-moving actions." Checkout placement writes "Exactly one audit record per placed order" (services/checkout/orders/create.py:49-50).
  - Order cancel is a smaller state change, and it has no gateway route (services/gateway/routes.py:14-28).
- **Suggested fix**: Remove the gateway route until the product team decides who can refund. If clients must refund, call `require_scope` with a refund scope in the handler. Send the caller identity to checkout. In checkout, compare `buyer_id` with the caller, or let only support staff use the route. Write an audit record with the actor in `refund_order`.
- **Effort**: Medium (hours)

### [Critical] Settlement drops the seller payout adjustment, and the adjustment has the wrong sign
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:23-26, :60 (head f61de6b)
- **Problem**: The seller payout does not change after a refund of a settled order. The marketplace pays the full refund, and the seller keeps the payout. Every refund of a settled order loses that money, and no error shows it. The adjustment row has these defects:
  - The row uses reason `'refund'`. Settlement accepts only `refund_correction`, `chargeback`, `goodwill`, and `fee_waiver` (services/settlement/payouts/adjustments.py:7-9). `validate_adjustment` returns False, and `merge_adjustments` skips the row and logs no error (adjustments.py:14-15, :29-30).
  - The amount is positive. `merge_adjustments` adds `amount_cents` to `gross_cents` (adjustments.py:34). If a later change fixes only the reason, settlement pays the seller the refund amount a second time.
  - The amount is the buyer refund, and that refund includes tax. It is not the seller gross for the order (`settled_orders.gross_cents`, services/settlement/cron.py:30).
  - Settlement skips an adjustment for a seller with no settled orders on that day (adjustments.py:31-33).
  - `CURRENT_DATE` uses the time zone of the database session. Settlement selects `day = previous_utc_day()` (cron.py:36, :42; services/settlement/windows.py:8-11).
- **Evidence**: `VALUES ($1, $2, 'refund', CURRENT_DATE)` (refund.py:25) and `await conn.execute(_INSERT_ADJUSTMENT, seller["seller_id"], amount_cents)` (refund.py:60). On the settlement side: `if adjustment.get("reason") not in VALID_REASONS: return False` and `entry["gross_cents"] += adjustment["amount_cents"]`.
- **Suggested fix**: Move this logic into settlement (see the next finding). If it stays in checkout, write a negative amount. Use a reason that settlement accepts, and write the seller gross from `settled_orders` in place of the buyer refund. Write the day in UTC. Add a test that sends the row that `refund_order` writes through `merge_adjustments`.
- **Effort**: Medium (hours)

### [High] Checkout reads and writes tables that belong to settlement
- **Category**: Architectural fit
- **Location**: services/checkout/orders/refund.py:22-26, :56-60 (head f61de6b)
- **Problem**: Checkout reads `settled_orders` and inserts into `payout_adjustments`. Settlement owns both tables. Settlement says that adjustment rows "come from the support tooling table" (adjustments.py:3). Checkout does not know the settlement rules: the reason values, the sign, the gross amount, and the split between sellers. The previous finding shows that checkout got each of these rules wrong. The same block has two more defects:
  - `fetchrow` on `_SELLER_QUERY` takes one row and ignores the others. If an order has lines from more than one seller, one seller gets the full adjustment.
  - If no `settled_orders` row exists, `seller` is `None` and line 60 raises `TypeError`. The route catches only `CheckoutError` (services/checkout/routes.py:74), so the client gets an unformatted 500.
- **Evidence**: In this repo, a service gets data from a different service through an HTTP client (services/checkout/clients/inventory_client.py:26-33) or through a stream event that the owning service consumes (services/pricing/consumer/loop.py:40). No other module in checkout reads or writes a settlement table. A grep for `settled_orders` and `payout_adjustments` finds only refund.py in checkout.
- **Suggested fix**: Delete the settled branch from `refund_order`. Let settlement read the `refunds` table in its nightly run, or let settlement consume a refund event. Let settlement build the adjustment with its own rules.
- **Effort**: Medium (hours)

### [High] The refund amount is not the amount the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35, :52 (head f61de6b)
- **Problem**: The order row already stores `total_cents`, the amount the buyer paid (services/checkout/orders/create.py:44, :91; services/checkout/orders/txn.py:7-9). The PR ignores that value and computes a new amount from `items_json`. The new formula has three errors:
  - It does not include shipping. Orders under 7500 cents pay 599 to 1299 cents for shipping (services/checkout/carts/totals.py:10-19, :35-39). The buyer does not get this back.
  - It does not include the promo discount (create.py:78-79; services/checkout/promotions.py:31-33). The buyer gets the discount as extra money.
  - It truncates each line with `int()` on a float. Checkout rounds tax with `round()` (totals.py:27-32). I ran the two formulas. 100 cents at 0.15 tax gives 114, but the buyer paid 115. 1099 cents at 0.07 gives 1175, but the buyer paid 1176. 2999 x 3 at 0.06 gives 9536, but the buyer paid 9537.
- **Evidence**:
  - First example: one line at 1999 cents, region `us`, tax 0.0725. The buyer paid 1999 + 145 + 599 = 2743 cents. The refund is 2143 cents, so the buyer loses 600 cents.
  - Second example: a subtotal of 10000, tax 0.08, and a 2000-cent amount promo. The buyer paid 8800 and gets 10800 back, so the overpayment is 2000 cents.
  - The formula also copies money math that `carts/totals.py` already has. It also reads the 25 KB `items_json` (services/checkout/orders/models.py:3-5) to get a value that the row already holds.
- **Suggested fix**: Select `total_cents` in `_LOCK_ORDER` and refund that value. Delete `refund_amount_cents`. Remove `items_json` from the query.
- **Effort**: Small (< 1 hour)

### [High] The `order.refunded` webhook can never reach a subscriber
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:69; services/checkout/webhooks/registry.py:8-15 (head f61de6b)
- **Problem**: `add_subscription` rejects each topic that is not in `KNOWN_TOPICS` (registry.py:36-37). `KNOWN_TOPICS` holds `order.placed`, `order.reserved`, `order.settled`, and `order.cancelled`. The PR does not add `order.refunded`. No integrator can subscribe to the topic, so `subscribers_for("order.refunded")` always returns an empty list. `dispatch` sends nothing.
- **Evidence**: `if topic not in KNOWN_TOPICS: raise ValidationFailure(["unknown topic %s" % topic])` (registry.py:36-37).
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that subscribes to the topic.
- **Effort**: Small (< 1 hour)

### [High] The stream event stays in a process buffer, and a restart drops it
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:68 (head f61de6b)
- **Problem**: `publish` only adds the event to a buffer in the process. The buffer goes to Redis when it holds `PUBLISH_FLUSH_COUNT = 200` entries (libs/eventkit/publisher.py:28-34; libs/eventkit/config.py:10). Checkout never calls `flush`. Its cleanup hook only closes the pool (services/checkout/app.py:30-34). This line is the only `publish` call in the repo.
  - At 3 refunds per minute, an event waits about 67 minutes.
  - A deploy or a crash loses up to 199 events.
  - At the 200th event, `flush` runs a synchronous Redis pipeline on the aiohttp event loop (publisher.py:20-25, :41-50).
  - The type `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES` (libs/eventkit/schema.py:29-44).
- **Evidence**: publisher.py:5-7 says that producers "call ``flush`` on their own schedule (request end, batch end, shutdown)". Checkout sends placements and cancellations only through `dispatch` (create.py:52, :71). It does not publish them on the stream. Thus this is the first checkout stream event, and no consumer exists in the repo.
- **Suggested fix**: Decide if a stream event is necessary now. If not, delete line 68. If yes, call `flush()` after `publish` through `asyncio.to_thread`, because the client is synchronous. Call `flush()` in `_on_cleanup`. Add the type to `KNOWN_EVENT_TYPES`.
- **Effort**: Small (< 1 hour)

### [High] Migration 0008 locks `orders` against writes while it builds an index that no query uses
- **Category**: Performance
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-24, :31-33 (head f61de6b)
- **Problem**: `CREATE INDEX` without `CONCURRENTLY` takes a SHARE lock on `orders`. This lock blocks each INSERT and UPDATE on `orders` until the build is complete. Checkout writes about 800 orders per minute, or 13 per second at peak (README.md:13). Settlement reads about 1.2M order rows per day (README.md:16), so the table is large and the build takes a long time. The docstring says that the script runs "during the 2.12 release", so it runs while the service takes live traffic. Order placement stops until the build is complete. Also, no query in the PR uses `(status, updated_at)`. The index is on `orders`, not on `refunds`, and the "refunds dashboard" is not in the repo. The index adds write cost to each status update, because each update changes both indexed columns (txn.py:16; refund.py:21).
- **Evidence**: Migration 0006 creates indexes only on new, empty tables (services/checkout/migrations/0006_add_webhooks.py:22-25, :40-44). This migration builds its index on a large table that takes live writes.
- **Suggested fix**: Delete `_CREATE_STATUS_INDEX` from this PR. Add the index in the same PR as the dashboard query that uses it. Use `CREATE INDEX CONCURRENTLY` in a separate script, outside a transaction.
- **Effort**: Small (< 1 hour)

### [Medium] The gateway refund handler does not use the response envelope or map upstream errors
- **Category**: API & interface design
- **Location**: services/gateway/handlers/orders_proxy.py:55-60 (head f61de6b)
- **Problem**: The handler returns `web.json_response(resp.json(), status=resp.status_code)`. This body has no `{"status", "data"}` envelope. The response also does not have the six security headers, `cache-control: no-store` included (services/gateway/responses.py:10-17). Clients get a different body shape on this one order route. The handler also does not map 5xx responses. Suppose checkout raises an uncaught exception, for example the `TypeError` in the settlement-table finding. aiohttp then returns a plain-text 500, `resp.json()` raises, and the gateway also returns an unformatted 500.
- **Evidence**: responses.py:3-4: "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints." The sibling handlers do this mapping: orders_proxy.py:26-30, :39-41, :48-52.
- **Suggested fix**: Map 404 and 5xx the same way that `get_order` does. Return `responses.envelope(resp.json(), status=resp.status_code)`. Remove the new `from aiohttp import web` import.
- **Effort**: Small (< 1 hour)

### [Medium] A refund of a placed order does not release the inventory holds
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:44-55; services/checkout/orders/models.py:47 (head f61de6b)
- **Problem**: The PR lets a `placed` order move to `refunded`. For the same `placed` order, `cancel_order` calls `release_order(order_id)` before it changes the status (create.py:69-70). `refund_order` does not call `release_order`. If the order still holds stock, that stock stays held after the refund. The reserve step says that holds expire only "if the order never lands" (services/checkout/orders/reserve_step.py:4).
- **Evidence**: `await release_order(order_id)` followed by `await update_status(order_id, "cancelled")` (create.py:69-70). refund.py has no equivalent call.
- **Suggested fix**: Call `release_order` when the old status is `placed`, as `cancel_order` does. If the business does not need this case, remove `refunded` from the `placed` set and let clients cancel a placed order (see Open Questions).
- **Effort**: Small (< 1 hour)

### [Medium] The tests check the wrong amount and do not run `refund_order`
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:24-29 (head f61de6b); PR-wide
- **Problem**:
  - The amount test checks the wrong value, items plus tax. The value `3240 + 700` has an exact float result, so the test cannot find the truncation error. It also cannot find the missing shipping charge or the missing promo discount.
  - No test runs `refund_order`. Thus no test covers the lock path, the not-found path, the settled branch, the adjustment row, the webhook topic, or the stream event.
  - No test covers the gateway handler.
  - A test that sends the adjustment row through `merge_adjustments` fails today. A test that subscribes to `order.refunded` also fails today.
- **Evidence**: `self.assertEqual(refund_amount_cents(_ITEMS), 3240 + 700)` (test_refund.py:26). The transition tests (test_refund.py:32-39) only read back the table in models.py.
- **Suggested fix**: Replace the amount tests with a test that the refund equals the stored `total_cents`. Add `refund_order` tests with a fake connection. The repo already has "FakeConn replays" (tests/fixtures/orders.py:30). Add contract tests against `validate_adjustment` and `add_subscription`.
- **Effort**: Medium (hours)

### [Medium] The PR description makes claims that the diff does not support
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: A reviewer who reads only the description gets a wrong model of the change:
  - "The refund amount is the full amount the buyer was charged" is false. The amount does not include shipping or the promo, and it truncates tax.
  - "the seller's payout is corrected in the next settlement run" is false. Settlement drops the row, and the sign is wrong.
  - "an `order.refunded` webhook" is not usable, because no subscriber can register for the topic.
  - "the same way they see placements and cancellations" is false. Checkout does not publish placements or cancellations on the stream.
  - "an index for the refunds dashboard" is not accurate. The index is on `orders`, and no dashboard query is in the repo.
  - The description does not say that any authenticated client can refund any order. This is an under-claim.
  - The row-lock claim is correct.
- **Evidence**: See the findings above for each line reference.
- **Suggested fix**: Fix the code first. Then make the description match the final behavior, and state who can call the route.
- **Effort**: Small (< 1 hour)

### [Low] The status list and the endpoint list do not include the refund
- **Category**: Maintainability
- **Location**: services/checkout/config.py:22; services/checkout/README.md:25-33 (head f61de6b)
- **Problem**: `ORDER_STATUS_VALUES` still lists five statuses, and `refunded` is not one of them. This list and `_NEXT_STATUSES` (models.py:44-51) now disagree. No code reads `ORDER_STATUS_VALUES` today, so a future reader of this list gets wrong data. The README endpoint list also does not show the refund route.
- **Evidence**: `ORDER_STATUS_VALUES = ("pending", "reserved", "placed", "settled", "cancelled")`.
- **Suggested fix**: Add `"refunded"` to `ORDER_STATUS_VALUES`. Add the refund route to the README endpoint list.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Concurrency**: `SELECT ... FOR UPDATE` in one `transaction()` (refund.py:44-55) stops a double refund. A second request waits for the lock, then reads `refunded` and gets a 400. The author's claim is correct.
- **Injection**: All new SQL uses `$n` parameters. No string formatting goes into SQL.
- **Resource leaks**: `transaction()` returns the connection on all paths (libs/dbkit/txn.py:28-32). The migration uses `await get_pool()`, which is correct (libs/dbkit/pool.py:30). Migrations 0006:39 and 0007:25 do not use `await`. That defect was in the code before this PR.
- **Scope & PR hygiene**: The PR does one feature. It has no unrelated edits and no reformatted files.
- **Performance on the request path**: The refund path runs a few times per minute. It has no N+1 query.
- **Observability**: The audit gap is part of the Critical security finding. The route logs nothing on rejection. The `cancel_order` route also logs nothing on rejection, so this is consistent with that route.

## Open Questions
1. Who may refund an order: the buyer, support staff, or both? Is there a time limit on a refund? A product decision answers this question and tells which fix applies to the security finding.
2. Which system returns the money to the buyer? The repo has no payment-provider call. If a different system reads the `refunds` table, state which system it is and what amount column it trusts.
3. What is the difference between `placed` → `refunded` and `placed` → `cancelled`? Has the buyer paid when an order is `placed`? Does a placed order still hold inventory? The answer decides if `placed` → `refunded` must exist and if it must call `release_order`.
4. Can one order contain lines from more than one seller? Which component writes `settled_orders` and moves an order to `settled`? Neither one is in this repo. Does that writer lock the order row and skip refunded orders? If it does not, a refund of a `placed` order can race with settlement.
5. How many rows does `orders` hold, and does the 2.12 migration run while checkout takes live traffic? These facts give the lock duration for the index build.
6. Is the time zone of the database session UTC? If it is not, `CURRENT_DATE` puts adjustments on the wrong settlement day.