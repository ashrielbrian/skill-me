Reviewing **acme/bazaar#1**, "checkout: full-order refunds", at head `f61de6b`.

# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout and a matching route in the gateway that forwards the request unchanged. The checkout function does these steps:
- It locks the order row and checks the status move.
- It computes an amount from `items_json`.
- It writes a `refunds` row and sets the status to `refunded`.
- For a settled order, it writes a `payout_adjustments` row.

After the commit, the function queues a Redis stream event and sends a webhook. A new migration creates the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says the PR adds full-order refunds for placed and settled orders. The refund amount is "the full amount the buyer was charged". A row lock stops two refunds of one order. For settled orders, the next settlement run corrects the seller payout. A stream event and an `order.refunded` webhook let downstream services and integrators see refunds. The migration adds the `refunds` table and "an index for the refunds dashboard".

## The underlying problem
Clients have no way to refund a full order. The buyer must get back the amount that they paid. When the order is already settled, the platform must also recover the payout from the seller.

The file set is close to a minimal solution. But the PR adds two parts that a minimal solution does not need: the stream event and the `orders` index. It also leaves out three edits that the feature needs: the webhook topic list, the reason list in settlement, and the auth checks in the gateway.

## Summary
The outline is sound: one domain function, one checkout route, one gateway route, and one migration. But four of the claimed effects do not work:
- The refund amount is not the amount that the buyer paid.
- The payout correction for the seller never applies.
- The webhook topic cannot get subscribers.
- The stream event can wait about an hour before delivery.

The most important finding: the gateway lets any authenticated session refund any order. It has no scope check, tenant check, or ownership check.

## Findings

### [Critical] Any authenticated session can refund any order
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55 (head f61de6b)
- **Problem**: The new public route forwards the refund with no scope check, no tenant header, and no ownership check. Checkout also does no ownership check. `refund.py:15` selects `buyer_id` but never uses it. Thus any holder of a valid bearer token, in any tenant, can refund any order whose ID they know.

  Order IDs are not secret. Every webhook payload sends `order_id` to third-party URLs (`create.py:96-104`, `refund.py:62-69`). A buyer can also refund their own settled (delivered) order with no approval step. A refund moves money to the buyer and is meant to take money back from the seller.
- **Evidence**:
  - `orders_proxy.py:55-60` has no `require_scope`, no session check, and no `x-tenant-id` header.
  - The sibling `place_order` sends `headers={"x-tenant-id": session.tenant_id}` (`orders_proxy.py:23-25`).
  - `middleware/auth.py:24-28` defines `require_scope` with the docstring "Called by handlers that guard writes." `handlers/tenants.py:26` uses it.
  - The middleware requires only a bearer token for paths that are not public (`auth.py:36-41`). It does not limit which orders a session can change.
- **Suggested fix**: Call `require_scope` with a refund scope at the start of the handler. Forward the session identity to checkout. Under the row lock, compare `orders.buyer_id` with the caller, or limit the route to a support scope. Decide whether a buyer may refund a settled order without approval.
- **Effort**: Medium

### [High] Migration 0008 locks the `orders` table to build an index that no code uses
- **Category**: Scope & PR hygiene
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-33 (head f61de6b)
- **Problem**: The migration runs a plain `CREATE INDEX` on `orders`, in the same transaction that creates `refunds`. A plain `CREATE INDEX` holds a SHARE lock. This lock stops every INSERT and UPDATE on `orders` until the build ends. Checkout writes about 800 orders per minute at peak (`services/checkout/README.md`), and each row holds about 25 KB of `items_json` (`orders/models.py:3-5`).

  The migration gets its connection from the shared pool. The pool sets `statement_timeout` to 5000 ms (`libs/dbkit/config.py:15`, `libs/dbkit/pool.py:44-47`). Two results are possible:
  - The build takes more than 5 s. Postgres stops it, and the rollback also removes the `refunds` table. The refund endpoint then ships with no table, and every refund call returns 500.
  - The build takes less than 5 s. Order placement and status updates still wait for that time.

  No query in the PR filters `orders` by `status` and `updated_at`. The PR body says the index is "for the refunds dashboard", but the dashboard is not in this PR. Also, the index is on `orders`, not on `refunds`. The `refunds` table gets no index on `order_id`.
- **Evidence**: The migration defines `CREATE INDEX orders_status_updated_idx ON orders (status, updated_at)` at :22-24 and runs it at :33, inside `conn.transaction()` at :31. The precedent `0006_add_webhooks.py:22-25` adds an index in the same transaction that creates its table. That table is empty, so its lock costs nothing.
- **Suggested fix**: Remove `_CREATE_STATUS_INDEX` from this PR. Ship it with the dashboard in its own migration. Use `CREATE INDEX CONCURRENTLY` outside a transaction, with no statement timeout. Add `UNIQUE (order_id)` to `refunds`. This puts the one-refund-per-order rule in the database.
- **Effort**: Small

### [High] Refunds of settled orders never reduce the seller payout
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:23-26 and :56-60 (head f61de6b)
- **Problem**: Three defects stop the adjustment from working:
  1. The reason `'refund'` is not in `VALID_REASONS` (`services/settlement/payouts/adjustments.py:7-9`). `merge_adjustments` skips rows with an unknown reason (`adjustments.py:14-15`, `:29-30`). Settlement ignores every refund adjustment and gives no error.
  2. The sign is wrong. `merge_adjustments` adds `amount_cents` to the seller gross (`adjustments.py:34`). The PR writes a positive amount. After a fix to item 1, the seller would get more money, not less.
  3. The amount is the buyer amount, which includes tax. The seller gross for the order is in `settled_orders.gross_cents` (`services/settlement/cron.py:29-32`).

  `merge_adjustments` also skips an adjustment when the seller has no settled orders on that day (`adjustments.py:31-33`). That skip is older code, but this PR now depends on it. A refund for a seller with no sales on the next day is lost too.

  Result: for each refunded settled order, the seller keeps the full payout, and no log shows it. The PR body says "the seller's payout is corrected in the next settlement run". The code does not do this.
- **Evidence**: The insert has `VALUES ($1, $2, 'refund', CURRENT_DATE)`. Settlement has `VALID_REASONS = frozenset({"refund_correction", "chargeback", "goodwill", "fee_waiver"})` and `entry["gross_cents"] += adjustment["amount_cents"]`.
- **Suggested fix**: Write a negative amount equal to the seller's `gross_cents` for the order. Use a reason that settlement accepts, or add `"refund"` to `VALID_REASONS` in this PR. Decide how settlement handles an adjustment for a seller with no orders that day. Add a test that sends the refund row through `merge_adjustments`.
- **Effort**: Medium

### [High] The refund amount is not the amount the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35, :52 (head f61de6b)
- **Problem**: `refund_amount_cents` computes the amount again from `items_json`. Its result is different from the placement total in three ways:
  - It leaves out shipping. When the subtotal is below 7500 cents, `grand_total_cents` adds 599 to 1299 cents of shipping (`carts/totals.py:9-19`, `:35-46`).
  - It ignores the promo discount. `_order_total` applies the promo to the grand total (`create.py:75-80`). A refund of a discounted order can pay back more than the buyer paid.
  - It truncates each line with `int()`. Placement rounds the tax on each line with `round()` (`totals.py:27-32`).

  The function also copies the money math in `carts/totals.py` with a different rounding rule. The `orders` table already stores the amount charged in `total_cents` (`txn.py:6-9`, `create.py:84-92`). The PR body says "The refund amount is the full amount the buyer was charged". The code does not do this.
- **Evidence**: I ran both formulas on the PR test items: 2 × 1500 cents at 0.08, and 1 × 700 cents at 0.0, region `us`.
  - Placement charges 4539 cents. The refund pays 3940 cents, so the buyer loses 599 cents (the shipping).
  - With a 20 percent promo, placement charges 3631 cents. The refund still pays 3940 cents, which is 309 cents too much.
  - One line of 1999 cents at 0.07: the refund pays 2138, and placement charged 2139. One line of 100 cents at 0.15: the refund pays 114, and placement charged 115.
  - `test_refund.py:26` asserts `3240 + 700`. The test therefore keeps the shipping gap.
- **Suggested fix**: Delete `refund_amount_cents`. Select `total_cents` in `_LOCK_ORDER` and refund that value. This change also removes the read of 25 KB of `items_json` under the row lock.
- **Effort**: Small

### [Medium] The stream event has no flush, and it can change a committed refund into a 500
- **Category**: Architectural fit
- **Location**: services/checkout/orders/refund.py:68 (head f61de6b)
- **Problem**: `refund.py` is the first `publish` caller in the repository, and checkout has no flush hook (`services/checkout/app.py`). `publish` only adds the event to a buffer in the process. It flushes when the buffer holds 200 events (`libs/eventkit/config.py:11`, `libs/eventkit/publisher.py:28-34`). The effects are:
  - At 3 refunds per minute, the first event waits about 67 minutes. A restart or deploy drops the buffer, and no log records the loss.
  - The 200th refund runs `flush()` inline, with the sync `redis.Redis` client (`publisher.py:18-25`). This blocks the aiohttp event loop for every checkout request.
  - If Redis fails during that flush, `pipe.execute()` raises an error that is not a `CheckoutError`. The client gets a 500 for a refund that already committed, and the webhook does not go out. A retry gets 400 "cannot be refunded".
  - `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES` (`libs/eventkit/schema.py:29-44`).

  The PR body says that downstream services see refunds "the same way they see placements and cancellations". Placements and cancellations send only webhooks and no stream event (`create.py:52`, `create.py:71`). No consumer of a checkout stream exists in the repository.
- **Suggested fix**: Remove the `publish` call. Keep the webhook as the only notification, as placement and cancellation do. If a consumer needs a stream event, add it in a separate PR. That PR must register the type, flush at the end of each request and at shutdown, and keep Redis I/O off the event loop.
- **Effort**: Small

### [Medium] The gateway refund response does not follow the edge envelope contract
- **Category**: API & interface design
- **Location**: services/gateway/handlers/orders_proxy.py:58-60 (head f61de6b)
- **Problem**: The handler returns `web.json_response(resp.json(), status=resp.status_code)`. Every sibling handler returns through `responses.envelope` or `responses.failure`. The effects are:
  - The body has no `{"status", "data"}` wrapper. Clients need a special parser for this one route.
  - The response has none of the six security headers in `responses.py:10-17`, which include `cache-control: no-store`.
  - When a checkout 5xx has a body that is not JSON, `resp.json()` raises, and the gateway returns an unhandled 500. Siblings change a 5xx into a 502 `upstream_error`.
  - A POST with no body makes `await request.json()` raise in both services. `reason` is optional, so a POST with no body is a normal call, and it fails with 500.

  The route is new and public, so this shape becomes a client contract when the PR merges.
- **Evidence**: `responses.py:1-4` says: "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints." The sibling handlers are at `orders_proxy.py:26-30`, `:39-41`, and `:48-52`.
- **Suggested fix**: Return through `responses.envelope(resp.json(), status=resp.status_code)`. Change a 5xx into `responses.failure("upstream_error", ...)`, as the siblings do. Accept an empty body in both services.
- **Effort**: Small

### [Medium] A concurrent cancel can overwrite a committed refund
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:44-55; services/checkout/orders/create.py:63-70 (head f61de6b)
- **Problem**: The row lock in `refund_order` stops only a second refund. `cancel_order` reads the status with no lock. It then calls the inventory service, and then writes the status with no condition. This sequence is possible:
  1. `cancel_order` reads `placed`.
  2. `cancel_order` waits on `release_order` for up to `INVENTORY_TIMEOUT_S = 10` seconds.
  3. `refund_order` locks the row, sees `placed`, writes the refund, sets `refunded`, and commits.
  4. `cancel_order` sets the status to `cancelled`.

  The result is a `refunds` row on a `cancelled` order, and subscribers get both `order.cancelled` and `order.refunded`. The cancel path without a lock is older code. This PR adds a second exit from `placed` that races with it, so the PR makes the race reachable.
- **Evidence**: `txn.py:16` defines `_UPDATE_STATUS = "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1"`, which has no status condition.
- **Suggested fix**: Make each status write conditional. Use `... WHERE id = $1 AND status = $3`, and fail when the write changes zero rows. Alternatively, take the same `FOR UPDATE` lock in `cancel_order`.
- **Effort**: Small

### [Medium] `order.refunded` webhooks cannot reach any subscriber
- **Category**: Intent vs implementation
- **Location**: services/checkout/orders/refund.py:69 (head f61de6b)
- **Problem**: `dispatch` sends only to `webhook_subscriptions` rows for the topic. `add_subscription` refuses any topic that is not in `KNOWN_TOPICS` (`webhooks/registry.py:8-15`, `:36-37`), and `order.refunded` is not in that set. `POST /v1/webhooks` with topic `order.refunded` returns 400 "unknown topic order.refunded". Each refund dispatch therefore finds zero URLs. The PR body says that integrators see refunds as they see placements and cancellations. The code does not do this.
- **Evidence**: `KNOWN_TOPICS = frozenset({"order.placed", "order.reserved", "order.settled", "order.cancelled"})`.
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that registers this topic.
- **Effort**: Small

### [Medium] Refunds write no audit record
- **Category**: Observability
- **Location**: services/checkout/orders/refund.py:38-70; services/gateway/handlers/orders_proxy.py:55-60 (head f61de6b)
- **Problem**: `libs/commerce/audit.py:1-6` says that the module writes "Audit records for money-moving actions", and that the settlement export reads them. Order placement writes an audit record in checkout (`create.py:50-51`) and in the gateway (`orders_proxy.py:28-29`). A refund moves money, but neither service writes an audit record for it. Operators cannot find who refunded an order.
- **Suggested fix**: Write an audit record with the actor, order ID, refund ID, and amount. Write it in the same services that write the placement audit.
- **Effort**: Small

### [Medium] The tests cover only a pure helper and the status table
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:25-39 (head f61de6b)
- **Problem**: The tests check `refund_amount_cents` and `can_transition` only. No test runs `refund_order`, the settled path, the checkout route, or the gateway handler. A test that sends the refund adjustment through `merge_adjustments` would have found the `VALID_REASONS` gap and the sign error. The amount test asserts `3240 + 700`, which is the wrong total. I could not run the test file here: its imports need `asyncpg`, and this environment does not have it.
- **Suggested fix**: Add tests for `refund_order` with a fake connection. Cover four cases: not found, invalid status, placed, and settled. Assert the rows that each case writes. Add a settlement test for the refund adjustment. Add a gateway test for the scope check.
- **Effort**: Medium

### [Low] The README and the status config do not show the new route and status
- **Category**: Maintainability
- **Location**: services/checkout/README.md (Endpoints section); services/checkout/config.py:22 (head f61de6b)
- **Problem**: The endpoint list in the README does not include `POST /v1/orders/{order_id}/refund`. `ORDER_STATUS_VALUES` does not include `"refunded"`. No code reads `ORDER_STATUS_VALUES` today, so this has no runtime effect.
- **Suggested fix**: Add the route to the README. Add `"refunded"` to `ORDER_STATUS_VALUES`, or delete the unused constant.
- **Effort**: Small

## Issues Not Found
Best practices & idioms and Performance have no findings other than the ones above. The new code logs no sensitive data, and every client value in the SQL is a bound parameter.

## Open Questions
1. **What sends money back to the buyer?** No code in the PR calls a payment provider. The PR only writes a `refunds` row. If no job reads `refunds`, the endpoint marks orders as refunded but pays nothing. To answer this, name the consumer of the `refunds` table.
2. **Can one order have items from more than one seller?** Each `settled_orders` row has a `seller_id` and an `order_id` (`cron.py:29-32`). `_SELLER_QUERY` uses `fetchrow` with no `ORDER BY`, so it takes one seller at random. If no row exists, `seller["seller_id"]` raises `TypeError`, and the refund fails with 500. To answer this, give the key of `settled_orders` and the rule for when a row is written.
3. **For a `placed` order, how is a refund different from a cancel?** `cancel_order` releases the inventory holds (`create.py:69`), but `refund_order` does not. A product decision answers this. It must say whether a refund of an order that has not shipped returns the stock.
4. **What moves an order from `placed` to `settled`?** No code in this repository does it. If that writer does not lock the row or check the status, settlement can pay for an order that is already `refunded`. To answer this, give the location of that writer.
5. **Who may refund?** The answer is buyers (self-service) or support staff only. It sets the scope name for the Critical finding.
6. **Does the checkout deployment set `BAZAAR_REDIS_URL`?** The eventkit default is `redis://redis.internal.bazaar:6379/0`. The checkout config uses `CHECKOUT_REDIS_URL`, with the default `redis://redis.internal:6379/2`. This matters only if the stream event stays.
7. **What is the time zone of the database session?** The adjustment uses `CURRENT_DATE`, and settlement reads `previous_utc_day()`. If the time zone is not UTC, the days do not match. This matters only after the adjustment works.
8. **How many rows does `orders` hold?** The row count gives the index build time and the lock time in the migration finding.