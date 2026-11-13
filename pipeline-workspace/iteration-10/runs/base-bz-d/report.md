# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout. It also adds a gateway route that sends the request straight to checkout. The new `refund_order` does these steps:
- It locks the order row and checks the status move.
- It calculates an amount from `items_json`, writes a `refunds` row, and sets the status to `refunded`.
- For a settled order, it writes a `payout_adjustments` row.

After the commit, it adds a Redis stream event to a queue and sends an `order.refunded` webhook. Migration 0008 adds the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says that the PR adds full refunds for placed and settled orders, at "the full amount the buyer was charged". The author says that a row lock stops two concurrent refunds of one order. The author also says that a refund of a settled order corrects the seller payout in the next settlement run. The author says that downstream services and integrators "see refunds the same way they see placements and cancellations". Migration 0008 adds an index "for the refunds dashboard".

## The underlying problem
Today, nobody can give a buyer their money back for an order. Also, a later payout does not take back the money that a seller got for that order.

My minimal sketch has these parts:
- A `refund_order` next to `cancel_order` in `services/checkout/orders/create.py`. It refunds the stored `orders.total_cents`.
- A negative adjustment with a reason that settlement accepts.
- One new webhook topic and the `refunds` table.
- A gateway proxy that checks scope, writes an audit record, and uses `responses`.

The diff touches about the same number of files as the sketch. But it adds three parts that the sketch does not need: a second amount calculation, a Redis stream event that nothing reads, and an index on `orders` that no code uses.

## Summary
The basic design is correct: one locked status move, a refund record, and a proxy route. But the PR is not safe to merge. Any user with a session can refund any order. The money flow is also wrong at each step: the amount ignores shipping, promos, and the checkout tax rounding, and settlement drops the seller adjustment. The most important finding is that the refund route has no authorization check.

## Findings

### [Critical] Any client with a session can refund any order
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55-60 (head f61de6b); services/checkout/orders/refund.py:15, :38-60 (head f61de6b)
- **Problem**: The refund route does not check who the caller is. The gateway middleware only makes sure that the request has a valid session (`services/gateway/middleware/auth.py:36-41`). The new proxy has these gaps:
  - It does not call `require_scope`.
  - It does not compare the caller to the order owner.
  - It does not send `x-tenant-id` or the caller ID to checkout.

  Checkout reads `buyer_id` under the lock (`refund.py:15`), but it never uses the value. Thus any session holder can refund any order ID of any buyer or tenant. A buyer can also refund their own settled order without approval from the seller or from support. For a settled order, the same call writes a payout adjustment against the seller. This new endpoint moves money, and the endpoint itself is new attack surface.
- **Evidence**:
  ```python
  async def refund_order(request):
      """Forwards a refund request to checkout. Runs a few times per minute."""
      order_id = request.match_info["order_id"]
      body = await request.json()
      resp = await _client.post("/v1/orders/" + order_id + "/refund", json=body)
  ```
  Sibling handlers use the session. `place_order` sends `session.tenant_id` (`orders_proxy.py:19-24`), and `list_orders` sends `session.user_id` (`orders_proxy.py:35-38`). The repo has a scope guard for this purpose: `require_scope` is "Called by handlers that guard writes." (`services/gateway/middleware/auth.py:24-28`). `services/gateway/handlers/tenants.py:26` uses it.
- **Suggested fix**: Decide who can issue a refund. Call `require_scope` in the proxy with a refund scope. Send the caller `user_id` and `tenant_id` to checkout. In checkout, compare them to the order row before the status move. As an alternative, put the route on an internal admin surface only.
- **Effort**: Medium

### [High] The refund writes no audit record
- **Category**: Architectural fit
- **Location**: services/gateway/handlers/orders_proxy.py:55-60; services/checkout/orders/refund.py:38-70 (head f61de6b)
- **Problem**: `libs/commerce/audit.py` holds "Audit records for money-moving actions." Order placement writes an audit record in checkout and in the gateway. A refund moves money to the buyer and away from the seller, but neither layer writes an audit record. The `refunds` table also has no actor column. Thus nothing records who asked for a refund. With the Critical finding above, an operator cannot find the account that issued a bad refund.
- **Evidence**: `services/checkout/orders/create.py:49-50` (`# Exactly one audit record per placed order.` / `await write_audit(audit_context())`). `services/gateway/handlers/orders_proxy.py:28-29` (`await write_audit(ctx, actor_id=session.user_id, action="order.place")`). `libs/commerce/audit.py:1-6`.
- **Suggested fix**: Write one audit record for each refund. Include the action, the actor, the order ID, the refund ID, and the amount. Use the same call shape as the gateway `place_order`. Add an actor column to `refunds` if support must query by actor.
- **Effort**: Small

### [High] The refund amount is not the amount that the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35, :52 (head f61de6b)
- **Problem**: `refund_amount_cents` calculates the amount again from `items_json` as a sum of `int(line_cents * (1 + tax_rate))`. Checkout already stores the charged amount in `orders.total_cents`. The two values differ in three ways:
  1. The refund does not include shipping. An order under 7500 cents pays 599 to 1299 cents for shipping (`services/checkout/carts/totals.py:10-19`). The buyer does not get this money back.
  2. The refund does not take off the promo discount. `_order_total` applies `apply_promo` to the grand total (`services/checkout/orders/create.py:75-80`). A buyer who used a promo gets back more than they paid.
  3. `int()` truncates, but checkout rounds the tax for each line with `round()` (`carts/totals.py:27-32`). Example: for 1 x 1999 cents at 8.25 percent, the buyer paid 2164 and the PR refunds 2163. For 1 x 999 cents at 7 percent, the buyer paid 1069 and the PR refunds 1068.

  The seller payout adjustment uses the same wrong amount. The function also copies the tax calculation of `carts/totals.py`, with different rounding.
- **Evidence**: The order header has the charged total in `Order.total_cents` (`services/checkout/orders/models.py:39`). `_INSERT_ORDER` writes it (`services/checkout/orders/txn.py:6-10`). The PR test makes the wrong value the expected value. `test_refund.py:26` expects `3240 + 700` = 3940. For a US order with those items, the charged total is 3700 + 240 tax + 599 shipping = 4539.
- **Suggested fix**: Select `total_cents` in `_LOCK_ORDER` instead of `items_json`, and refund that value. Delete `refund_amount_cents`, the `json` import, and `RefundAmountTest`. This change also removes a read of about 25 KB of `items_json` per refund (`orders/models.py:3-5`).
- **Effort**: Small

### [High] Settlement drops the refund adjustment, and the sign is wrong
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:22-26, :56-60 (head f61de6b)
- **Problem**: For a settled order, the refund writes a `payout_adjustments` row with `reason = 'refund'` and a positive `amount_cents`. Settlement does not apply this row. The seller keeps the money, so the platform pays both the buyer and the seller for one order. These are the details:
  - `validate_adjustment` accepts only `{"refund_correction", "chargeback", "goodwill", "fee_waiver"}` (`services/settlement/payouts/adjustments.py:7-9, :14-15`). It skips `'refund'`.
  - `merge_adjustments` adds `amount_cents` to `gross_cents` (`adjustments.py:34`). If the reason were valid, the positive amount would make the seller payout larger by the refund amount.
  - `merge_adjustments` skips a seller who has no settled orders on that day (`adjustments.py:31-33`), so that adjustment is lost.
  - `_SELLER_QUERY` uses `fetchrow`. If `settled_orders` has no row for the order, `seller["seller_id"]` raises `TypeError` (`refund.py:59-60`). `services/checkout/routes.py:72-75` catches only `CheckoutError`, so the client gets an HTTP 500. If the order has rows for two or more sellers, the first seller gets the full amount.
- **Evidence**: `services/settlement/cron.py:34-37` and `:47-48` read `payout_adjustments WHERE day = $1` and give the rows to `merge_adjustments`. The adjustments module says "Adjustment rows come from the support tooling table" (`adjustments.py:3-4`). Checkout now writes to a table that settlement owns, and the row does not match the settlement contract.
- **Suggested fix**: Write a negative amount with a reason that settlement accepts, or add `'refund'` to `VALID_REASONS` in this PR. Raise a `CheckoutError` when no seller row exists. Make settlement keep an adjustment for a later run when the seller has no orders on that day. Add a test that gives the refund row to `merge_adjustments`. A better long-term design lets settlement own this write and get the refund from an event or an API.
- **Effort**: Medium

### [High] Integrators cannot subscribe to `order.refunded`
- **Category**: Intent vs implementation
- **Location**: services/checkout/orders/refund.py:69 (head f61de6b)
- **Problem**: The PR says that it sends an `order.refunded` webhook. `add_subscription` rejects each topic that is not in `KNOWN_TOPICS`, and the PR does not add `order.refunded` to that set. Thus no subscription for this topic can exist. `dispatch("order.refunded", ...)` always finds zero URLs and sends nothing.
- **Evidence**: `services/checkout/webhooks/registry.py:8-15` lists only `"order.placed"`, `"order.reserved"`, `"order.settled"`, `"order.cancelled"`. `registry.py:36-37`: `if topic not in KNOWN_TOPICS: raise ValidationFailure(["unknown topic %s" % topic])`.
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that registers a subscription for this topic.
- **Effort**: Small

### [High] Migration 0008 adds an unused index on `orders` and blocks order writes during the build
- **Category**: Performance
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-24, :31-33 (head f61de6b)
- **Problem**: `CREATE INDEX` without `CONCURRENTLY` takes a SHARE lock on `orders`. This lock blocks each `INSERT` and `UPDATE` on `orders` until the transaction commits. The migration builds the index in the same transaction as `CREATE TABLE refunds`.

  The table is large. Checkout writes about 800 orders per minute, with 13 per second at peak. Settlement reads about 1.2M order rows per day. Order placement and cancellation stop for the full build time.

  No code in the PR or in the repo queries `orders` by `(status, updated_at)`. The "refunds dashboard" in the PR body is not in the diff. The index also adds write cost to each order insert and to each status update.
- **Evidence**: `CREATE INDEX orders_status_updated_idx ON orders (status, updated_at)` runs inside `async with conn.transaction():`. The load numbers come from `README.md`: checkout "~800 orders/min", settlement "~1.2M order rows".
- **Suggested fix**: Remove the index from this PR. Add it in the PR that adds the dashboard query. If the index must ship, use `CREATE INDEX CONCURRENTLY` in a separate migration that does not run in a transaction.
- **Effort**: Small

### [Medium] The Redis stream event has no consumer and stays in the buffer
- **Category**: Simplicity & economy
- **Location**: services/checkout/orders/refund.py:68 (head f61de6b)
- **Problem**: Before this PR, checkout did not publish to a Redis stream. Placement and cancellation send only webhooks (`create.py:52`, `create.py:71`). Only `services/pricing/consumer/loop.py:40` calls `consume`, and it reads a pricing stream. Thus the new `checkout.order_refunded` event has no consumer, and the claim "the same way they see placements and cancellations" is not true. The event also fails in these ways:
  - `publish` only adds the event to a buffer. The buffer goes to Redis when it holds `PUBLISH_FLUSH_COUNT = 200` events (`libs/eventkit/config.py:10`, `libs/eventkit/publisher.py:33-34`). Checkout never calls `flush`. At a few refunds per minute, an event waits for about 200 refunds.
  - `_on_cleanup` does not call `flush` (`services/checkout/app.py:30-34`). Each deploy or restart loses the events in the buffer.
  - The 200th refund runs `flush` with a sync Redis pipeline on the event loop. If Redis fails, the error comes after the DB commit. The client gets an HTTP 500 for a completed refund, and the webhook is not sent. `_buffer.clear()` does not run (`publisher.py:50-51`), so each later refund tries the flush again and fails the same way.
  - `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES` (`libs/eventkit/schema.py:28-44`).
- **Evidence**: `publish(stream_name("checkout", "order"), make_envelope("checkout.order_refunded", event))`. The publisher says: "producers that need a bounded delivery delay call ``flush`` on their own schedule (request end, batch end, shutdown)" (`publisher.py:3-7`).
- **Suggested fix**: Delete the `publish` call until a consumer exists. If a consumer needs the event, do these steps:
  - Register the type in `KNOWN_EVENT_TYPES`.
  - Call `flush` at request end and at shutdown.
  - Do not let a stream error change the response for a committed refund. An outbox row in the same transaction gives at-least-once delivery.
- **Effort**: Small

### [Medium] The gateway refund proxy does not use the gateway response contract
- **Category**: API & interface design
- **Location**: services/gateway/handlers/orders_proxy.py:59-60 (head f61de6b)
- **Problem**: The new handler returns `web.json_response(resp.json(), status=resp.status_code)`. Each sibling handler returns through `responses.envelope` or `responses.failure`. Thus refund responses do not have the `{"status", "data"}` body. They also do not have the six security headers in `_HEADER_PAIRS`, which include `strict-transport-security` and `cache-control: no-store`.

  The handler also does not change an upstream 5xx into the 502 `upstream_error` body. Sometimes checkout returns its default text 500 page, for example after the `TypeError` in the payout finding. Then `resp.json()` raises in the gateway, and the client gets a second 500 with no envelope.
- **Evidence**: `services/gateway/responses.py:1-4` ("Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints."). Siblings: `get_order` at `orders_proxy.py:48-52`, `place_order` at `orders_proxy.py:26-30`.
- **Suggested fix**: Use the same shape as `get_order`. Map 404 and >= 500 with `responses.failure`. Return `responses.envelope(resp.json(), status=resp.status_code)`. Delete `from aiohttp import web`.
- **Effort**: Small

### [Medium] Cancel and refund can both apply to one placed order
- **Category**: Correctness
- **Location**: services/checkout/orders/models.py:47; services/checkout/orders/refund.py:21, :44-55 (head f61de6b)
- **Problem**: The PR adds `placed -> refunded` next to the existing `placed -> cancelled`. The refund locks the row, but `cancel_order` does not take a lock. It reads the status with a plain `SELECT`. Then it calls inventory over HTTP, which takes 5 to 20 ms. Then it writes the status with no status guard.

  A plain `SELECT` does not wait for a `FOR UPDATE` lock. Thus a cancel and a refund on one placed order can both pass their checks, and the last write wins. The order can end as `cancelled` with a `refunds` row, or as `refunded` with its stock released. The lock stops two refunds, but it does not stop a refund and a cancel. The same risk applies to the writer that sets `settled`, which is not in this repo.
- **Evidence**: `services/checkout/orders/create.py:62-70` (`fetch_one(_STATUS_QUERY, ...)`, `release_order`, `update_status`). `services/checkout/orders/txn.py:16` (`UPDATE orders SET status = $2, updated_at = now() WHERE id = $1`). `refund.py:21`: `_MARK_REFUNDED` also has no status guard.
- **Suggested fix**: Make each status write conditional, for example `... WHERE id = $1 AND status = ANY($3)`, and check the row count. As an alternative, make `cancel_order` lock the row with `FOR UPDATE`, as the refund does.
- **Effort**: Medium

### [Medium] The tests do not cover the refund flow
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:24-39 (head f61de6b)
- **Problem**: The tests cover only the pure amount function and the transition table. No test runs `refund_order`, the settled-order branch, the payout row, the webhook topic, the checkout route, or the gateway proxy. The amount test expects the wrong value: 3940, but the charged total for a US order is 4539. Thus each High finding above passes the test suite.
- **Evidence**: `self.assertEqual(refund_amount_cents(_ITEMS), 3240 + 700)`.
- **Suggested fix**: Add tests for `refund_order` with a fake connection. Cover these cases:
  - The order is not found.
  - The status is wrong.
  - The order is placed.
  - The order is settled. Assert the adjustment row and its sign.
  - The event payload.

  Add a test that gives the refund row to `merge_adjustments` and checks that settlement applies it. Add a test that `add_subscription("order.refunded", ...)` succeeds.
- **Effort**: Medium

### [Low] The `refunds` table does not enforce one refund for each order
- **Category**: Correctness
- **Location**: services/checkout/migrations/0008_add_refunds.py:14-20 (head f61de6b)
- **Problem**: This feature refunds the full order, so one order has one refund or none. The table has no `UNIQUE (order_id)` and no index on `order_id`. Only the row lock in `refund_order` enforces the rule. A support tool or a backfill that writes to `refunds` directly can add a second row. A query for the refund of one order must scan the full table.
- **Evidence**: `order_id TEXT NOT NULL REFERENCES orders (id)` with no unique constraint and no index.
- **Suggested fix**: Add `UNIQUE (order_id)` to the `CREATE TABLE`. The unique constraint also creates the index.
- **Effort**: Small

### [Low] The checkout status list does not include `refunded`
- **Category**: Maintainability
- **Location**: services/checkout/config.py:22 (head f61de6b)
- **Problem**: `ORDER_STATUS_VALUES` still has five statuses and does not include `refunded`. No code reads this constant today. But it now does not agree with `_NEXT_STATUSES` (`orders/models.py:44-51`).
- **Evidence**: `ORDER_STATUS_VALUES = ("pending", "reserved", "placed", "settled", "cancelled")`.
- **Suggested fix**: Add `"refunded"`, or delete the unused constant.
- **Effort**: Small

## Issues Not Found
- **SQL injection**: Each query uses `$n` parameters.
- **Refund ID**: `uuid.uuid4().hex` is not predictable.
- **Refund against refund**: `SELECT ... FOR UPDATE` in `READ COMMITTED` makes the second request wait. The second request then reads `refunded` and fails the transition check. The PR claim is correct for this case.
- **Transaction scope**: `libs.dbkit.transaction` rolls back on error and always returns the connection. The PR uses it correctly.
- **Migration pool call**: `await get_pool()` is correct. (`0006_add_webhooks.py:38` and `0007_split_addresses.py:24` call it without `await`, but this PR does not touch those files.)
- **Checkout route handler**: The shape is the same as the `cancel_order` sibling (`services/checkout/routes.py:58-65`).
- **Library imports**: The PR imports from the package roots, as `libs/dbkit/__init__.py:13-15` tells services to do.
- **Scope and PR hygiene**: The PR does one feature and has no unrelated edits.
- **AI-generated code signs**: The docstrings that give call rates follow the repo convention. I found no comments that only repeat the code and no unused guards.

## Open Questions
1. **What pays the money back to the buyer?** The repo has no payment integration. Maybe a payment system reads the stream event or the webhook. If so, the stream finding and the webhook finding become Critical. To answer this, name the system that sends the refund payment.
2. **Who can issue a refund?** The buyer, the seller, support staff, or an internal service? The answer sets the scope name and the ownership check for the Critical finding.
3. **How is `placed -> refunded` different from `placed -> cancelled`?** `cancel_order` releases the inventory holds (`create.py:69`), but the refund does not. `reserve_step.py:3-4` says that holds expire after ten minutes "if the order never lands". If fulfillment still ships a refunded placed order, the buyer gets the goods and the money. To answer this, state the fulfillment rule for a refunded placed order.
4. **Who sets `status = 'settled'` and writes `settled_orders`?** This repo does neither. If that writer does not guard the status, it can replace `refunded` with `settled` and pay the seller for a refunded order.
5. **Can one order have rows for more than one seller in `settled_orders`?** If yes, the adjustment must divide the amount between the sellers.
6. **Which amount should the seller lose?** The options are the buyer total with tax and shipping, or the seller `gross_cents` or `net_cents` in `settled_orders`. Settlement adds adjustments to `gross_cents` before fees (`adjustments.py:1-4`).
7. **Does the Postgres session use UTC?** `CURRENT_DATE` (`refund.py:25`) must match `previous_utc_day()` (`services/settlement/windows.py:8-11`). If the database uses local time, a refund near midnight can get a day that settlement already ran. Settlement then never reads that row.
8. **Is `libs/commerce/order_types.py` the source of refund rules?** It has a shared `refundable_cents` (`order_types.py:91-95`) with a different set of statuses (`paid`, `delivered`). It returns the subtotal, which also is not the charged amount. To answer this, state which order model that module serves.
9. **How many rows does `orders` hold now?** The row count sets how long the index build in migration 0008 holds the lock.