# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout. It also adds a gateway route that passes the request straight to checkout. The new `refund_order` function locks the order row and calculates an amount from `items_json`. It then writes a `refunds` row and sets the status to `refunded`. For a `settled` order, it also writes a `payout_adjustments` row. After the commit, it publishes a stream event and sends a webhook. Migration 0008 creates the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says the PR adds full-order refunds for placed and settled orders, exposed to clients through the gateway. The author makes these claims:
- The refund is "the full amount the buyer was charged".
- The row lock stops a double refund.
- The seller's payout "is corrected in the next settlement run".
- Downstream services and integrators "see refunds the same way they see placements and cancellations".
- The new index is "for the refunds dashboard".

## The underlying problem
An operator or a buyer must be able to give back the full charged amount for an order and mark the order as refunded. If the seller already got the money, the next payout must take it back. The size of the diff is close to the minimal shape: a checkout function and route, a `refunds` table, and a gateway route. But the payout correction is in the wrong service. The PR also adds two parts beyond the minimal shape: an `orders` index that nothing reads, and a stream producer with no consumer in the repo.

## Summary
The overall structure is correct: a locked status move in checkout, a `refunds` table, and thin routes. But almost every part that moves money or sends a notification is wrong. The most important finding: the gateway lets any authenticated session refund any order, with no scope check, no owner check, and no audit record. Also:
- The refund amount does not match the charged amount.
- Settlement drops the seller payout adjustment, and the adjustment has the wrong sign.
- The `order.refunded` webhook can never have a subscriber.

As a result, four claims in the description do not match the code.

## Findings

### [Critical] Any authenticated client can refund any order through the gateway
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55 (head f61de6b); services/gateway/routes.py:24
- **Problem**: The gateway handler sends the refund to checkout with no authorization check. The auth middleware only makes sure that a bearer token is present (services/gateway/middleware/auth.py:36-41). Checkout does not check the caller either: `refund.refund_order(order_id, reason)` gets no user ID and no tenant ID. Thus any buyer session, seller session, or admin-console session can refund any order if it knows the order ID. This endpoint moves money and changes seller payouts. The handler also writes no audit record, so an operator cannot find out who started a refund.

  Order IDs are `ord_` plus a uuid4 hex (services/checkout/orders/create.py:87), so a stranger cannot easily guess one. But webhook subscribers get order IDs, and `get_order` returns any order with no owner check (older code, orders_proxy.py:44-52). A buyer can also refund their own `settled` order after the seller got the money, with no approval step.
- **Evidence**: The new handler:
  ```
  async def refund_order(request):
      """Forwards a refund request to checkout. Runs a few times per minute."""
      order_id = request.match_info["order_id"]
      body = await request.json()
      resp = await _client.post("/v1/orders/" + order_id + "/refund", json=body)
      return web.json_response(resp.json(), status=resp.status_code)
  ```
  Precedents:
  - `require_scope` is "Called by handlers that guard writes" (services/gateway/middleware/auth.py:24-28). services/gateway/handlers/tenants.py:26 uses it.
  - `place_order` checks the session and writes an audit record (orders_proxy.py:19-21, 28-29 at head).
  - The gateway does not expose the lower-risk `/cancel` route at all (services/gateway/routes.py:21-24).
- **Suggested fix**: Decide who may refund. Call `require_scope(request, "<refund scope>")` at the start of the handler. If buyers may refund, send `session.user_id` to checkout. Compare it with `orders.buyer_id` while the row lock is held. Write an audit record with `action="order.refund"`. If clients do not need the route now, remove it and keep refunds internal, like cancel.
- **Effort**: Medium

### [High] Checkout writes into the settlement service's tables
- **Category**: Architectural fit
- **Location**: services/checkout/orders/refund.py:22-26, 56-60 (head f61de6b)
- **Problem**: `refund_order` reads `settled_orders` and inserts into `payout_adjustments`. Both tables belong to settlement, and no other checkout code touches them. Checkout now depends on the settlement schema and on three settlement rules: the reason vocabulary, the sign convention, and the amount basis. The PR gets all three rules wrong (next finding), which shows the cost of this coupling. Each later change to these settlement rules must also change checkout.
- **Evidence**:
  - Settlement reads only its own tables (services/settlement/cron.py:29-37).
  - Adjustment rows "come from the support tooling table" (services/settlement/payouts/adjustments.py:1-4).
  - Checkout and inventory share a database cluster, but checkout still changes inventory state through the HTTP client: `release_order` (services/checkout/clients/inventory_client.py:26-33).
  - The PR already publishes `checkout.order_refunded` with `order_id` and `amount_cents` (refund.py:62-68). Settlement can consume it.
- **Suggested fix**: Remove lines 56-60 and the two settlement queries from checkout. Let settlement consume the refund event, or read the `refunds` table. Settlement then writes its own adjustment from `settled_orders.gross_cents`, and it owns the reason, sign, and amount rules in one place. This fix needs a working event first (see the stream-event finding below).
- **Effort**: Medium

### [High] Settlement drops the payout adjustment, and its sign and amount are wrong
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:23-26, 59-60 (head f61de6b)
- **Problem**: The adjustment has three defects:
  1. Settlement rejects the reason. `_INSERT_ADJUSTMENT` writes the reason `'refund'`, which is not in `VALID_REASONS` (services/settlement/payouts/adjustments.py:7-9). `merge_adjustments` skips the row with no log line for that row (adjustments.py:29-30). The seller keeps the full payout, and `submit_transfers` sends it to the bank (cron.py:53).
  2. The sign is wrong. `merge_adjustments` does `entry["gross_cents"] += adjustment["amount_cents"]` (adjustments.py:34). The PR writes a positive amount. With a valid reason, the seller's gross goes up by the refund amount, not down.
  3. The basis is wrong. The PR writes the buyer amount with tax. Settlement paid the seller from `settled_orders.gross_cents`, and tax is not seller revenue.

  `merge_adjustments` also skips an adjustment when the seller has no settled orders on that day (adjustments.py:31-33). That code is older, but the PR depends on it. If a seller has no sales on the day of the refund, the correction is lost.
- **Evidence**: I ran `merge_adjustments` from the PR head with the row that the PR writes. The seller's starting gross was 10000:
  - `reason='refund'`, `amount_cents=3940` → 0 adjustments applied. The gross stays 10000.
  - `reason='refund_correction'`, `amount_cents=3940` → 1 applied. The gross becomes 13940.
- **Suggested fix**: Use the fix in the preceding finding. If the write stays in checkout, change three things. Use a reason from `VALID_REASONS`. Write the negative of `gross_cents` from the `settled_orders` row for that seller and order. Add a test that merges the row and checks that the gross goes down.
- **Effort**: Small

### [High] The refund amount is not the amount that the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35, 52 (head f61de6b)
- **Problem**: `refund_amount_cents` calculates a new total from `items_json`. That total is different from the charged amount in three ways:
  1. Shipping. `grand_total_cents` adds 599 to 1299 cents of flat shipping to each order under 7500 cents (services/checkout/carts/totals.py:35-45). The refund leaves out shipping.
  2. Promo. `apply_promo` lowers the total (create.py:75-80; promotions.py:16-33). The refund ignores the discount, so the buyer gets back more than they paid.
  3. Rounding. The order rounds the tax for each line with `round()` (totals.py:27-32). The refund applies `int()` to `line_cents * (1 + tax_rate)`, so float error can drop one cent per line.

  The order already stores the charged amount in `orders.total_cents` (create.py:44-45, 91; txn.py:7-9). The PR calculates the amount again instead of reading that value.
- **Evidence**: For the PR's own test items in region `us` (2 × 1500 cents at rate 0.08, 1 × 700 cents at rate 0.0):
  - Charged: 3700 + 240 tax + 599 shipping = 4539 cents. The refund is 3940 cents, which is 599 cents short.
  - With a 20 percent promo, the charge is 3631 cents. The refund is still 3940 cents, which is 309 cents too much.

  I also compared `int(p*(1+r))` with `p + round(p*r)` for 6300 (price, rate) pairs. 3112 pairs differed by one cent. Example: price 100 at rate 0.026 gives 102 and 103.
- **Suggested fix**: Select `total_cents` in `_LOCK_ORDER` and refund that value. Delete `refund_amount_cents`. Change its test to assert the stored total. This change also stops the read of `items_json`, which is about 25 KB for each order (models.py:3-5).
- **Effort**: Small

### [High] No integrator can subscribe to `order.refunded`
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:69 (head f61de6b)
- **Problem**: `dispatch("order.refunded", event)` sends the event to the subscribers of that topic. `add_subscription` rejects each topic that is not in `KNOWN_TOPICS`, and `order.refunded` is not in that set. Thus no subscription can exist, `subscribers_for` returns an empty list, and the webhook is never sent.
- **Evidence**: services/checkout/webhooks/registry.py:8-15 and 36-37:
  ```
  KNOWN_TOPICS = frozenset(
      {
          "order.placed",
          "order.reserved",
          "order.settled",
          "order.cancelled",
      }
  )
  ...
      if topic not in KNOWN_TOPICS:
          raise ValidationFailure(["unknown topic %s" % topic])
  ```
  `POST /v1/webhooks` with `{"topic": "order.refunded", ...}` returns 400 with "unknown topic order.refunded".
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that registers a subscription for the topic.
- **Effort**: Small

### [Medium] Migration 0008 adds an orders index that nothing reads, and the build can block order writes
- **Category**: Simplicity & economy
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-24, 31-33 (head f61de6b)
- **Problem**: No query in the PR filters `orders` by `status` and `updated_at`. The description says that the index is "for the refunds dashboard", but the PR has no dashboard. The index adds write cost to each order insert and status update, at about 800 orders per minute (services/checkout/README.md:7).

  The migration runs a plain `CREATE INDEX` in a transaction. Postgres takes a SHARE lock on `orders` for the build, which blocks placements and status writes. The migration uses the shared pool, which sets `statement_timeout` to 5000 ms (libs/dbkit/pool.py:43-46; libs/dbkit/config.py:16). At about 1.15 million new orders per day, the build can take more than 5 s. Postgres then cancels it and also rolls back `CREATE TABLE refunds`, so the refund endpoint fails on each call.
- **Evidence**: `_CREATE_STATUS_INDEX` is `CREATE INDEX orders_status_updated_idx ON orders (status, updated_at)`. It runs after `_CREATE_REFUNDS` in the same `async with conn.transaction()` block.
- **Suggested fix**: Remove the index from this PR. Add it in the same PR as the dashboard: run `CREATE INDEX CONCURRENTLY` outside a transaction, on a connection with no statement timeout. In this PR, add `UNIQUE (order_id)` to `refunds`. The constraint enforces one full refund per order and also indexes the foreign key.
- **Effort**: Small

### [Medium] The gateway refund response bypasses the standard envelope and the upstream error mapping
- **Category**: API & interface design
- **Location**: services/gateway/handlers/orders_proxy.py:60 (head f61de6b)
- **Problem**: All other gateway handlers return through `responses.envelope` or `responses.failure`. The new handler returns `web.json_response(resp.json(), ...)`. Clients get a different body shape for this one route, with no `{"status", "data"}` wrapper. The response also has none of the six security headers, so `cache-control: no-store` is not set. The handler does not change a checkout 5xx into a 502. If checkout sends aiohttp's plain-text 500 page, `resp.json()` raises an exception, and the gateway sends an unhandled 500.
- **Evidence**: services/gateway/responses.py:1-4: "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints." The sibling `get_order` maps 404 and 5xx and wraps the body (orders_proxy.py:48-52 at head). The new import `from aiohttp import web` (orders_proxy.py:7) exists only for this line.
- **Suggested fix**: Use the same shape as `get_order`. Change a 5xx to `responses.failure("upstream_error", "checkout did not answer", 502)`. Return `responses.envelope(resp.json(), status=resp.status_code)`. Remove the `aiohttp` import.
- **Effort**: Small

### [Medium] The stream event stays in memory until 200 events and is lost on restart
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:68 (head f61de6b)
- **Problem**: `publish` only adds the event to a buffer in the process. The buffer flushes when it holds `PUBLISH_FLUSH_COUNT` = 200 events (libs/eventkit/config.py:10; libs/eventkit/publisher.py:28-34). This line is the first `publish` call in checkout. Checkout never calls `flush`, and `_on_cleanup` does not flush (services/checkout/app.py:30-34). The docstring gives "a few per minute". At three refunds per minute, an event waits about 67 minutes, and a deploy or crash loses all buffered events.

  The flush on the 200th event runs a synchronous Redis pipeline on the event loop. If Redis fails at that time, the error reaches the route after the refund has committed. The client gets a 500 response, and a retry gets 400 "cannot be refunded". Also, `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES` (libs/eventkit/schema.py:28-44). That list documents "Event types currently on the wire".
- **Evidence**: libs/eventkit/publisher.py:6-7: "producers that need a bounded delivery delay call ``flush`` on their own schedule". No consumer in the repo reads `stream_name("checkout", "order")`.
- **Suggested fix**: First decide whether a consumer exists. If a consumer exists (for example, settlement, per the architectural-fit finding), do these steps:
  - Call `flush` after `publish`, through `asyncio.to_thread`.
  - Add the type to `KNOWN_EVENT_TYPES`.
  - Flush in `_on_cleanup`.

  If no consumer exists, remove the `publish` call from this PR.
- **Effort**: Small

### [Medium] A concurrent cancel can change `refunded` to `cancelled`
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:44-55 (head f61de6b); older code at services/checkout/orders/create.py:62-70 and services/checkout/orders/txn.py:16
- **Problem**: The refund lock works only if every writer of the order status takes the same lock. `cancel_order` reads the status with no lock. It then runs an unconditional `UPDATE orders SET status = $2, updated_at = now() WHERE id = $1`. That cancel code is older, but the PR's claim about concurrency depends on it. This order of events causes the failure:
  1. A cancel request reads status `placed` and passes `can_transition`.
  2. A refund request locks the row, writes the `refunds` row, sets `refunded`, and commits.
  3. The cancel request calls `release_order`, then sets `cancelled`.

  The result: the order status is `cancelled`, a `refunds` row exists, and subscribers get both `order.refunded` and `order.cancelled`.
- **Evidence**: create.py:62 uses `fetch_one(_STATUS_QUERY, ...)` with no `FOR UPDATE`. txn.py:16 has no status condition.
- **Suggested fix**: Add the expected status to the update: `... WHERE id = $1 AND status = $3`. Raise an error when the update changes 0 rows. Alternatively, take `FOR UPDATE` in `cancel_order` as well.
- **Effort**: Small

### [Medium] The tests do not run `refund_order`, and the amount test asserts the wrong value
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:25-26 (head f61de6b)
- **Problem**: The tests cover only the pure amount function and the transition table. No test covers these paths:
  - the lock path
  - `OrderNotFound`
  - the invalid-status error
  - the settled branch
  - the checkout route
  - the gateway handler

  The amount test asserts 3940 cents, but an order with those items in region `us` charges 4539 cents. Thus the test locks in the defect in the amount finding. A test that passed the adjustment row to `merge_adjustments` would have failed.
- **Evidence**: The repo has a fake pool for tests of database paths: `make_pool` in tests/conftest.py, used at tests/test_checkout_flow.py:58-79. I could not run `test_refund.py` locally, because `asyncpg`, `redis`, `httpx`, and `aiohttp` are not installed. I ran the formula and `merge_adjustments` directly instead.
- **Suggested fix**: Add async tests for `refund_order` with `make_pool` for four cases: not found, wrong status, `placed`, and `settled`. Assert that the refund amount equals `total_cents`. Add a settlement test that merges the adjustment and checks that the seller's gross goes down.
- **Effort**: Medium

### [Medium] Four claims in the description do not match the diff
- **Category**: Intent vs implementation
- **Location**: PR-wide
- **Problem**: These claims are false:
  1. "The refund amount is the full amount the buyer was charged". The code leaves out shipping and promo discounts (see the amount finding).
  2. "the seller's payout is corrected in the next settlement run". Settlement drops the row (see the adjustment finding).
  3. "so downstream services and integrators see refunds the same way they see placements and cancellations". Placements and cancellations only call `dispatch` (create.py:52, 71) and publish nothing on a stream. Also, the refund webhook cannot have subscribers (see the webhook finding).
  4. "an index for the refunds dashboard". The PR has no dashboard and no query that uses the index.

  "Tests cover the amount calculation" is true, but the test asserts the wrong amount.
- **Evidence**: See the cited findings above.
- **Suggested fix**: Fix the code. Then update the description, and remove the claims for parts that move to a later PR.
- **Effort**: Small

## Issues Not Found
No findings for Best practices & idioms, Error handling (apart from the error after commit in the stream-event finding), Performance, Observability, Maintainability, or Scope & PR hygiene (apart from the index). For Performance, I checked the lock. It holds one row for a short transaction at a few calls per minute, and no cost grows without a limit. The webhook fan-out after the commit follows the `cancel_order` precedent.

## Open Questions
1. **What sends the money back to the buyer?** The repo has no call to a payment provider. The only outputs are the `refunds` row, the event, and the webhook. The name of the system that reads them and returns the funds answers this question.
2. **Who may refund, and from which status?** Buyer self-service for a `settled` order is very different from a refund by support staff only. The product rule decides the scope check and the owner check in the Critical finding.
3. **How is a refund from `placed` different from a cancel from `placed`?** Cancel releases the inventory holds (create.py:69) and writes no `refunds` row. A refund writes a `refunds` row but does not release the holds. The intended inventory result of a refund from `placed` answers this question.
4. **Can one order have more than one seller?** `settled_orders` has a `seller_id` on each row (cron.py:29-31), and `fetchrow` (refund.py:59) takes one arbitrary row. If an order can have two sellers, the full adjustment goes to one of them. The `settled_orders` key or schema answers this question.
5. **Does each `settled` order have a `settled_orders` row at refund time?** If not, `seller` is `None`, and line 60 raises `TypeError`. The transaction then rolls back, and the client gets a 500. The order in which the writer sets `settled` and inserts `settled_orders` answers this question.
6. **Does `orders.status` have a CHECK constraint or an enum type?** The `orders` schema is not in the repo. If the constraint exists and does not include `refunded`, every `_MARK_REFUNDED` fails. The `orders` DDL answers this question.
7. **Does the process that writes `settled` lock the row or check the current status?** If it does neither, the race in the cancel finding also applies between settle and refund: the seller is paid and no adjustment is written. The code of that process answers this question.
8. **Is the database session time zone UTC?** `CURRENT_DATE` (refund.py:25) must match `previous_utc_day()` (services/settlement/windows.py:8-11). If they differ, a refund near midnight can go to a different settlement day. The `TimeZone` setting of the cluster answers this question.