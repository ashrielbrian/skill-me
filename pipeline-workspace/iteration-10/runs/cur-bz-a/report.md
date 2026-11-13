# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to the checkout service, and the public gateway forwards the same path to it. The checkout code locks the order row, calculates an amount from `items_json`, writes a `refunds` row, and sets the status to `refunded`. For a `settled` order, it also writes a `payout_adjustments` row. After the commit, it queues a `checkout.order_refunded` stream event and sends an `order.refunded` webhook. Migration 0008 adds the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says that the PR adds full refunds for `placed` and `settled` orders, and that clients can call it through the gateway. The author also makes these claims:
- The amount is "the full amount the buyer was charged for the order".
- A row lock stops two concurrent requests from refunding one order.
- For a settled order, "the seller's payout is corrected in the next settlement run".
- Downstream services and integrators "see refunds the same way they see placements and cancellations".
- The new index is for "the refunds dashboard".

## The underlying problem
Nobody can return the money for an order, and the order status and the seller payout cannot show a refund. The minimal change is one function next to `cancel_order` (services/checkout/orders/create.py:56). It locks the order, reads `orders.total_cents`, writes a refund row, sets the status, and calls `dispatch`. The change also needs an authorized route, a registered webhook topic, and a payout correction that settlement accepts.

The diff is about the same size as that sketch. But it adds three parts that the sketch does not need:
- a second money calculation
- a stream event that nothing reads
- an index for a dashboard that is not in the repo

It also leaves out three parts that the sketch needs:
- authorization
- a valid adjustment
- a registered webhook topic

## Summary
The code does not do what the description says. The most important finding: anyone with a valid session token, in any tenant, can refund any buyer's order through the gateway. Three claims in the description are false in the code:
- The amount is not the amount the buyer was charged.
- Settlement drops the seller adjustment, and the adjustment has the wrong sign.
- No subscriber can get the `order.refunded` webhook.

Fix the Critical and High findings before merge.

## Findings

### [Critical] Anyone with a valid token can refund any order through the gateway
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55 (head f61de6b); services/gateway/routes.py:24 (head f61de6b); services/checkout/orders/refund.py:44 (head f61de6b)
- **Problem**: The PR puts an action that moves money on the public edge, and no layer checks who may do it. The auth middleware only checks that the request has a valid bearer token (services/gateway/middleware/auth.py:36-41). The gateway handler does not read the session, does not check a scope, does not send `x-tenant-id`, and does not write an audit record. The checkout code reads `buyer_id` (refund.py:15) but never compares it with the caller.

  Example: user A in tenant X sends `POST /v1/orders/ord_<id>/refund` with A's own token, for an order of buyer B in tenant Y. Checkout moves B's order to `refunded`. If the order is `settled`, checkout also writes a payout adjustment against B's seller. No audit record shows that A did it.

  Order IDs are not secret. The access log writes `request.path` (services/gateway/middleware/accesslog.py:34), and status URLs and webhooks carry the ID. Before this PR, the gateway exposed no order status change after placement: it has no cancel route (services/gateway/routes.py:21-23).
- **Evidence**:
  ```python
  async def refund_order(request):
      """Forwards a refund request to checkout. Runs a few times per minute."""
      order_id = request.match_info["order_id"]
      body = await request.json()
      resp = await _client.post("/v1/orders/" + order_id + "/refund", json=body)
      return web.json_response(resp.json(), status=resp.status_code)
  ```
  Other handlers check the session or a scope before a write or a scoped read:
  - `place_order` checks the session (orders_proxy.py:19-21) and writes an audit record (orders_proxy.py:28-29).
  - `list_orders` limits the read to `session.user_id` (orders_proxy.py:38).
  - The docstring of `require_scope` (services/gateway/middleware/auth.py:24-28) says "Called by handlers that guard writes." services/gateway/handlers/tenants.py:26, :42, and :52 call it.
- **Suggested fix**: Decide who may refund (see Open Questions). Call `require_scope` with a refund scope in the gateway handler. Send the session `user_id` and `tenant_id` to checkout. In checkout, compare them with the order owner inside the locked transaction. Write an audit record as `place_order` does. If only operators may refund, remove the gateway route. Keep the endpoint internal, as cancel is.
- **Effort**: Medium (hours)

### [High] Migration 0008 builds an unused index on `orders` with a lock that blocks order writes
- **Category**: Simplicity & economy
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-33 (head f61de6b)
- **Problem**: No code in the PR or in the repo reads `orders_status_updated_idx`. The description says that it is for "the refunds dashboard", but there is no dashboard in the repo. A dashboard can also query the new `refunds` table, which has `created_at`.

  The index also has an operational cost:
  - A plain `CREATE INDEX` takes a SHARE lock on `orders`. The lock blocks every INSERT and UPDATE on the table until the build ends.
  - Checkout writes about 800 orders per minute (about 13 per second at peak), with a p50 budget of 90 ms (services/checkout/README.md). Settlement reads about 1.2M order rows per day (services/settlement/README.md). Thus the table is large.
  - The migration gets its connection from the shared pool. The pool sets `statement_timeout` to 5000 ms (libs/dbkit/config.py:16, libs/dbkit/pool.py:45).

  On a table of this size, the build will probably take more than 5 s. The statement then stops, the transaction rolls back, and the `refunds` table is not created. If the endpoint ships anyway, each refund fails with a database error. If an operator increases the timeout and runs the migration again, order placement stops for the full build time. The index also adds one index write to each order insert and each status update, and nothing reads it.
- **Evidence**:
  ```python
  _CREATE_STATUS_INDEX = """
      CREATE INDEX orders_status_updated_idx ON orders (status, updated_at)
  """
  ...
          async with conn.transaction():
              await conn.execute(_CREATE_REFUNDS)
              await conn.execute(_CREATE_STATUS_INDEX)
  ```
  The earlier migration services/checkout/migrations/0006_add_webhooks.py:22-25 indexes only the table that it creates. That table is empty at build time, so no writes wait on the lock.
- **Suggested fix**: Delete `_CREATE_STATUS_INDEX` from this PR. Add the index in the PR that adds the dashboard query. In that PR, use `CREATE INDEX CONCURRENTLY` outside a transaction, with a higher `statement_timeout`. In this migration, add `UNIQUE (order_id)` to `refunds`. A full-order refund occurs only once for each order, and the constraint also gives an index for lookups by order.
- **Effort**: Small (< 1 hour)

### [High] The refund amount is not the amount that the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35 and :52 (head f61de6b)
- **Problem**: `refund_amount_cents` calculates the amount again from `items_json`. Its result is different from the placement total in three ways:
  1. It leaves out shipping. Placement adds 599 to 1299 cents to each order under 7500 cents (services/checkout/carts/totals.py:10-19, :42-45).
  2. It ignores the promo discount. Placement subtracts the discount from the total (services/checkout/orders/create.py:75-80).
  3. It uses `int()` on a float, which truncates. Placement uses `round()` on the tax of each line (services/checkout/carts/totals.py:27-32).

  The order row already stores the charged amount in `orders.total_cents` (services/checkout/orders/txn.py:7-9; services/checkout/orders/create.py:44-45). The PR does not read it.

  I ran the PR formula and the placement formula on these inputs:
  - Shipping: one line, 1999 cents, quantity 1, `tax_rate` 0.0825, region `us`. The buyer paid 1999 + 165 + 599 = 2763 cents. The refund is 2163 cents, which is 600 cents too low.
  - Promo: subtotal 8000 cents, `tax_rate` 0.08, amount promo of 1000 cents. The buyer paid 8640 − 1000 = 7640 cents. The refund is 8640 cents, which is 1000 cents more than the payment.
  - Float: one line, 100 cents, `tax_rate` 0.15, shipping not counted. `100 * 1.15` is `114.99999999999999`, so the refund is 114 cents. The buyer paid 115 cents.

  The same wrong amount goes into the seller adjustment (refund.py:60) and into both events. The test at services/checkout/tests/test_refund.py:26 passes only because its inputs have no shipping, no promo, and exact float results.
- **Evidence**:
  ```python
  def refund_amount_cents(items):
      """Amount to return to the buyer: every line with its tax."""
      total = 0
      for entry in items:
          line_cents = entry["unit_price_cents"] * entry["quantity"]
          total += int(line_cents * (1 + entry["tax_rate"]))
      return total
  ```
- **Suggested fix**: Add `total_cents` to `_LOCK_ORDER`. Use it as the refund amount. Delete `refund_amount_cents` and its tests. Remove `items_json` from the locked select. The code then no longer reads the 25 KB product snapshot (services/checkout/orders/models.py:3-5).
- **Effort**: Small (< 1 hour)

### [High] Settlement drops the seller adjustment, and the adjustment has the wrong sign
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:22-26 and :56-60 (head f61de6b)
- **Problem**: The description says "the seller's payout is corrected in the next settlement run". Settlement does not do that, for two reasons.

  First, the reason `'refund'` is not in `VALID_REASONS` (services/settlement/payouts/adjustments.py:7-9). `merge_adjustments` skips each row that fails `validate_adjustment`, and it raises no error (adjustments.py:28-30). Thus settlement ignores every refund adjustment. The seller keeps the full payment for each refunded settled order.

  Second, the amount has the wrong sign. `merge_adjustments` adds `amount_cents` to the seller gross (adjustments.py:34), and the PR writes a positive amount. If someone adds `'refund'` to `VALID_REASONS`, settlement will pay the seller the refund amount a second time.

  The design also crosses a service boundary. Checkout writes directly into a settlement table. Settlement says that this table holds "Adjustment rows come from the support tooling table" (adjustments.py:3-4). Checkout also reads `settled_orders`, which settlement owns (services/settlement/cron.py:29-32). Checkout does not know the seller's gross, so the PR uses the buyer's amount with tax in its place. The seller's gross and net come from `settled_orders` (cron.py:30).

  Other effects:
  - If a `settled` order has no `settled_orders` row, `seller` is `None` and refund.py:60 raises `TypeError`. The transaction rolls back, and the client gets a 500.
  - `merge_adjustments` also skips a seller with no settled orders on that day (adjustments.py:31-33). Thus settlement also drops the refund for a seller who had no sales that day. This behavior was there before the PR, but the PR depends on it.
- **Evidence**:
  ```python
  _INSERT_ADJUSTMENT = """
      INSERT INTO payout_adjustments (seller_id, amount_cents, reason, day)
      VALUES ($1, $2, 'refund', CURRENT_DATE)
  """
  ```
  ```python
  VALID_REASONS = frozenset(
      {"refund_correction", "chargeback", "goodwill", "fee_waiver"}
  )
  ...
          entry["gross_cents"] += adjustment["amount_cents"]
  ```
- **Suggested fix**: Let settlement own the payout correction. One option: settlement reads the refund event, then writes a negative adjustment from the seller gross in `settled_orders`. If checkout must write the row, write a negative amount. Use a reason that `validate_adjustment` accepts, for example `refund_correction`. Add a test that runs `merge_adjustments` on the row. Return a clear error when the `settled_orders` row is absent.
- **Effort**: Medium (hours)

### [High] No subscriber gets the refund webhook, and the stream event can wait more than an hour
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:68-69 (head f61de6b)
- **Problem**: The description says that integrators "see refunds the same way they see placements and cancellations". The code does not do that.

  The webhook: `order.refunded` is not in `KNOWN_TOPICS` (services/checkout/webhooks/registry.py:8-15). `add_subscription` rejects an unknown topic with `ValidationFailure` (registry.py:36-37). Thus no integrator can subscribe to it. `subscribers_for("order.refunded")` always returns an empty list, and `dispatch` sends nothing.

  The stream event: `publish` only adds the event to a buffer in the process (libs/eventkit/publisher.py:28-34). The buffer flushes at 200 entries (`PUBLISH_FLUSH_COUNT`, libs/eventkit/config.py:10), or when the producer calls `flush()`. No code in the repo calls the eventkit `publish()` or `flush()`, except this PR. At 3 refunds per minute, the first flush occurs after about 67 minutes. A deploy or a crash in that time loses the queued events. The flush also makes a blocking Redis call from the sync client on the event loop (publisher.py:20-25, :50).

  Placements and cancellations publish no stream events. They only call `dispatch` (services/checkout/orders/create.py:52, :71). Thus the stream event is a new pattern for checkout, not "the same way". The type `checkout.order_refunded` is also not in `KNOWN_EVENT_TYPES` (libs/eventkit/schema.py:29-44).
- **Evidence**:
  ```python
      publish(stream_name("checkout", "order"), make_envelope("checkout.order_refunded", event))
      await dispatch("order.refunded", event)
  ```
  ```python
  KNOWN_TOPICS = frozenset(
      {
          "order.placed",
          "order.reserved",
          "order.settled",
          "order.cancelled",
      }
  )
  ```
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Delete the stream publish if no consumer reads it. The settlement option in the previous finding is one possible consumer. If you keep it, add the type to `KNOWN_EVENT_TYPES`. Call `flush()` after `publish()` through `asyncio.to_thread`.
- **Effort**: Small (< 1 hour)

### [Medium] The gateway handler does not use the response envelope or the upstream error check
- **Category**: Architectural fit
- **Location**: services/gateway/handlers/orders_proxy.py:60 (head f61de6b)
- **Problem**: The handler returns `web.json_response` directly. All the other handlers return through `responses.envelope` or `responses.failure`. This causes three problems:
  - The response has none of the six security headers in services/gateway/responses.py:10-17, for example `cache-control: no-store` and `strict-transport-security`.
  - The body has no `{"status", "data"}` envelope. A client that reads `data` from every gateway response fails on this route.
  - The handler does not check for a status of 500 or more. When checkout returns a 500, the aiohttp body is plain text, so `resp.json()` raises `json.JSONDecodeError`. The gateway then returns its own unhandled 500. The other handlers return a 502 `upstream_error` body.

  This PR already has one path to a checkout 500: a `settled` order with no `settled_orders` row raises `TypeError` at services/checkout/orders/refund.py:60. The `from aiohttp import web` import at orders_proxy.py:7 exists only for this call.
- **Evidence**: `return web.json_response(resp.json(), status=resp.status_code)`. The other handlers in the same file show the usual pattern at orders_proxy.py:26-30, :39-41, and :48-52. The module docstring at services/gateway/responses.py:1-5 says: "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints."
- **Suggested fix**: Use the same structure as `place_order`. Return a 502 `responses.failure` when the status is 500 or more. Map 404 to `not_found`, as `get_order` does. Return `responses.envelope(resp.json(), status=resp.status_code)`. Delete the `aiohttp.web` import.
- **Effort**: Small (< 1 hour)

### [Medium] A concurrent cancel can overwrite `refunded` with `cancelled`
- **Category**: Correctness
- **Location**: services/checkout/orders/models.py:47 (head f61de6b); services/checkout/orders/refund.py:44-55 (head f61de6b)
- **Problem**: The row lock stops two refunds of one order, but it does not stop a concurrent cancel. `cancel_order` reads the status with no lock (services/checkout/orders/create.py:62). Next, it calls inventory over HTTP, which can take up to 10 s (`INVENTORY_TIMEOUT_S`). Then it writes the status with no check on the old status (services/checkout/orders/txn.py:16). The PR adds the move `placed` → `refunded`, so both paths now accept a `placed` order.

  The failure sequence for one `placed` order is:
  1. Cancel reads `placed` and calls `release_order`.
  2. Refund locks the row, sees `placed`, writes the refund row, sets `refunded`, and commits.
  3. Cancel runs its status update and overwrites `refunded` with `cancelled`.

  The result is a `cancelled` order that has a `refunds` row. Subscribers get both `order.cancelled` and `order.refunded`. The lock claim in the description is true only for two refund requests. The cancel path was there before the PR, but the PR adds a second path that changes the status of a `placed` order.
- **Evidence**: `_UPDATE_STATUS = "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1"` (txn.py:16).
- **Suggested fix**: Make both status writes depend on the old status, for example `WHERE id = $1 AND status = $3`. Return a conflict when the update changes zero rows. Alternatively, lock the order row in `cancel_order` with the same `FOR UPDATE` transaction.
- **Effort**: Small (< 1 hour)

### [Medium] A refund of a `placed` order does not release the inventory hold
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:55 (head f61de6b)
- **Problem**: The cancel path releases the stock hold for a `placed` order (services/checkout/orders/create.py:69). The refund path accepts the same `placed` status but does not release the hold. The reservation step says that holds expire only "if the order never lands" (services/checkout/orders/reserve_step.py:4). A placed order did land, so its hold stays. Thus the stock for each refunded `placed` order stays reserved, and other buyers cannot buy it.
- **Evidence**: `cancel_order` calls `await release_order(order_id)` before the status update (create.py:69). `refund_order` makes no inventory call.
- **Suggested fix**: When the old status is `placed`, call `release_order` after the commit, as cancel does. If a `placed` order can already be shipped, state that rule in the PR description instead (see Open Questions).
- **Effort**: Small (< 1 hour)

### [Medium] The tests do not run the refund flow, and one test confirms the wrong amount
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:24-41 (head f61de6b)
- **Problem**: The tests cover only `refund_amount_cents` and the transition table. No test calls `refund_order`, the checkout route, or the gateway route. Thus no test catches these defects:
  - the absent authorization check at the gateway
  - the adjustment that settlement drops
  - the unregistered webhook topic
  - a `settled` order with no `settled_orders` row

  `test_sums_every_line_with_tax` uses inputs with no shipping, no promo, and exact float results. Thus it confirms the wrong formula.
- **Evidence**: `self.assertEqual(refund_amount_cents(_ITEMS), 3240 + 700)` (test_refund.py:26). The repo already has a fake pool for async database tests: `make_pool` (tests/conftest.py:10) and `FakePool` (tests/helpers/fakes.py:52). `PlacementRoundTripTest` uses them (tests/test_checkout_flow.py:58).
- **Suggested fix**: Add an async test of `refund_order` with `make_pool`. Use an order with shipping and a promo, and assert that the refund amount equals `total_cents`. Assert that the adjustment row passes `validate_adjustment` and lowers the seller gross in `merge_adjustments`. Add a gateway test that rejects a caller who does not own the order.
- **Effort**: Medium (hours)

## Issues Not Found
No findings: API & interface design, Best practices & idioms, Error handling, Performance, Observability, Maintainability, Scope & PR hygiene, Intent vs implementation.
- API & interface design, Best practices & idioms, Error handling, and Performance have no separate findings. Their defects are in the findings above: the gateway 500 and the sync Redis flush.
- Intent vs implementation has no separate finding. The amount, payout, and webhook findings each show a claim in the description that the code does not do.
- Scope & PR hygiene: the PR does one feature and has no unrelated edits.

## Open Questions
1. **Who may refund?** It can be the buyer, the seller, or support staff. Can a buyer refund a `settled` order with no approval? The answer sets the scope check, and it decides whether the route belongs on the public gateway at all.
2. **Where does the money go back to the buyer?** The PR writes a `refunds` row. The repo has no payment provider client, and no code reads `refunds`. Name the process that returns the money, and say whether it reads this table.
3. **Can one order have more than one seller?** If `settled_orders` has one row for each seller in an order, `fetchrow` at refund.py:59 takes one seller and charges the full amount to that seller. The `settled_orders` schema answers this.
4. **Which process moves an order from `placed` to `settled`?** No code in the repo writes `settled`. That process might not check the status. If so, it can settle an order that was refunded while `placed`, pay the seller, and write no adjustment.
5. **Is the database time zone UTC?** The adjustment uses `CURRENT_DATE`, and settlement reads `previous_utc_day()` (services/settlement/cron.py:42, :47). Suppose the database uses America/New_York. A refund at 03:00 UTC on day D then gets day D-1. Settlement processed day D-1 at 02:00 UTC, so it never sees the row.
6. **Can a `placed` order already be shipped?** If it can, the inventory finding changes: the hold must be used up, not released.