# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout and adds the same route to the public gateway. A new module, `orders/refund.py`, locks the order row, calculates an amount from `items_json`, writes a `refunds` row, and sets the status to `refunded`. For a settled order, it also reads `settled_orders` and writes a `payout_adjustments` row. After the commit, it publishes a Redis stream event and sends an `order.refunded` webhook. Migration 0008 adds the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says that the PR adds full-order refunds for placed and settled orders, and that clients can call the endpoint through the gateway. The author makes these claims:
- The refund amount is "the full amount the buyer was charged for the order".
- A row lock stops two concurrent refunds of one order.
- The next settlement run corrects the seller payout.
- Downstream services and integrators "see refunds the same way they see placements and cancellations".
- The migration adds an index "for the refunds dashboard".

## The underlying problem
Today the platform cannot record a full refund for a placed or settled order. It also cannot take the refund amount back from the seller payout. The minimal sketch has these parts:
- One domain function next to `cancel_order` in `services/checkout/orders/create.py`, plus one checkout route.
- One change to `_NEXT_STATUSES`.
- The amount from the stored `orders.total_cents`.
- The `order.refunded` topic added to `KNOWN_TOPICS`.
- A migration for the `refunds` table only.
- A gateway route with a scope check and an ownership check.
- The seller correction done in settlement, which owns payouts.

The diff has about the same file count as the sketch. But it carries three parts beyond the sketch: a new Redis stream producer, an index with no reader, and direct writes into settlement tables.

## Summary
The PR has a clear structure. But the public endpoint has no authorization, and the code that moves money does not do what the description says. The most important finding is a security gap: any caller with a valid session token can refund any order through the gateway, with no ownership check or scope check. Three claims in the description are false: the refund amount, the seller payout correction, and the webhook for integrators.

## Findings

### [Critical] Any session holder can refund any order through the public gateway
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55 (head f61de6b); also services/gateway/routes.py:24 and services/checkout/orders/refund.py:15
- **Problem**: The new gateway handler sends the refund to checkout with no authorization. The auth middleware only makes sure that the request has a valid bearer token (`services/gateway/middleware/auth.py:36-41`). The handler does not call `require_scope`. It does not compare the session user with the buyer of the order, and it does not send `x-tenant-id`. Checkout does not check ownership either. `_LOCK_ORDER` reads `buyer_id` at `refund.py:15`, but no code uses the value.

  The results:
  - A user who knows an order ID can refund an order that belongs to a different buyer. Order IDs are in webhook payloads (`services/checkout/orders/create.py:98-99`), so they are not secret.
  - A buyer can refund their own settled order in full, with no approval and no return of goods.
  - The PR intends each refund of a settled order to reduce the seller payout, so the seller pays for the loss.
  - No audit record shows who asked for the refund. The `refunds` table has no column for the actor.
- **Evidence**:
  - New handler (`orders_proxy.py:55-60`): `body = await request.json()`, then `resp = await _client.post("/v1/orders/" + order_id + "/refund", json=body)`. It does not read `request.get("session")`.
  - Sibling write path: `place_order` reads the session (`orders_proxy.py:18-20`). It sends `headers={"x-tenant-id": session.tenant_id}` (`orders_proxy.py:23`). It writes an audit record with `action="order.place"` (`orders_proxy.py:27-28`).
  - Scope precedent: `require_scope(request, "tenant:read")` at `services/gateway/handlers/tenants.py:26`.
  - The gateway does not expose the cancel endpoint, which is less dangerous. `services/gateway/routes.py:21-24` has no cancel route, but checkout registers it at `services/checkout/routes.py:25`.
- **Suggested fix**: Remove the gateway route until the team agrees on who can refund. If clients must call it, do these steps:
  - Call `require_scope` with a refund scope in the gateway handler.
  - Send `x-tenant-id` and the session user ID to checkout.
  - In checkout, reject the request when `buyer_id` does not match the caller.
  - Write an audit record with the actor ID.
  - Store the actor in the `refunds` row.
- **Effort**: Medium

### [High] Checkout writes settlement tables directly
- **Category**: Architectural fit
- **Location**: services/checkout/orders/refund.py:22 (head f61de6b); also refund.py:23-26 and refund.py:56-60
- **Problem**: Checkout now reads `settled_orders` and inserts rows into `payout_adjustments`. Settlement owns both tables and the rules for them. The settlement code says "Adjustment rows come from the support tooling table" (`services/settlement/payouts/adjustments.py:3`). Before this PR, checkout wrote only to its own tables. The new dependency ties checkout to the settlement schema and the settlement validation rules. The finding "Settlement drops every refund adjustment" shows the cost: checkout does not know those rules, so settlement ignores every row that checkout writes.
- **Evidence**:
  - Today checkout writes only to `orders`, `order_items`, `carts`, `cart_items`, `addresses`, and `webhook_subscriptions`. It reads and writes no settlement table.
  - Precedent for an effect in a different service: `cancel_order` calls `release_order` (`services/checkout/orders/create.py:69`). `release_order` calls the inventory service over HTTP (`services/checkout/clients/inventory_client.py:26-33`).
  - Precedent for settlement reading checkout data: `items_json` "is written at order placement and read by the settlement export" (`services/checkout/orders/models.py:3-5`).
  - Settlement already merges adjustments in its own nightly run (`services/settlement/cron.py:47-48`).
- **Suggested fix**: Make settlement own the payout correction. Let settlement read the `refunds` table, or a refund event. Let settlement write the adjustment with its own reason, sign, and seller split. Delete `_SELLER_QUERY` and `_INSERT_ADJUSTMENT` from `refund.py`.
- **Effort**: Medium

### [High] The refund amount is not the amount the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29 (head f61de6b); also refund.py:52 and services/checkout/tests/test_refund.py:26
- **Problem**: `refund_amount_cents` calculates the total again from `items_json`. It uses different rules from the charge, so the amount differs in three ways:
  1. It does not include shipping. Checkout charges 599 to 1299 cents of shipping when the subtotal is less than 7500 cents (`services/checkout/carts/totals.py:10-19`, `totals.py:35-39`).
  2. It ignores promo codes. Checkout applies the promo to the grand total (`services/checkout/orders/create.py:75-80`). A buyer who used a promo gets back more money than they paid.
  3. It truncates a float with `int()`. The charge uses `round()` on the tax of each line (`totals.py:27-32`). For a 1999-cent line at a 0.0825 tax rate, the refund is 2163 cents and the charge was 2164 cents. For a 100-cent line at 0.15, the refund is 114 cents and the charge was 115 cents.

  The PR test makes the wrong value the expected result. It expects 3940 cents. A `us` order with these items was charged 3940 + 599 = 4539 cents.
- **Evidence**: The orders table already stores the charged amount. `_build_order` sets `total_cents=total_cents` (`create.py:91`), and `_INSERT_ORDER` writes it (`services/checkout/orders/txn.py:6-10`). The PR copies money math that `carts/totals.py` already owns. That module says "each line's tax rounds on its own so the stored lines sum exactly to the stored total" (`totals.py:3-4`).
- **Suggested fix**: Select `total_cents` in `_LOCK_ORDER`. Refund that value. Delete `refund_amount_cents`. Stop the parse of `items_json`, which is about 25 KB per order (`models.py:3-4`). Change the test to assert that the refund equals the stored `total_cents`.
- **Effort**: Small

### [High] Settlement drops every refund adjustment, and the sign and amount are wrong
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:25 (head f61de6b); also refund.py:59-60
- **Problem**: The adjustment row never changes a payout. The row has these defects:
  1. The row uses the reason `'refund'`. Settlement accepts only `refund_correction`, `chargeback`, `goodwill`, and `fee_waiver`. `merge_adjustments` skips all other rows and shows no error.
  2. With a valid reason, the row would increase the payout, not decrease it. `merge_adjustments` adds `amount_cents` to `gross_cents`, and the PR writes a positive amount. The seller would get the refund amount a second time.
  3. The amount is the buyer total with tax. The seller `gross_cents` in `settled_orders` is a different figure. So the deduction would not match the money that the seller got.
  4. `fetchrow` returns one row. If an order has rows for more than one seller, one seller gets the full adjustment.
  5. If the order has no `settled_orders` row, `seller` is `None`. Then `seller["seller_id"]` raises `TypeError`. The transaction rolls back, and the client gets a plain-text 500 with no error mapping.
  6. `merge_adjustments` also skips the row when the seller has no settled orders on that day.
- **Evidence**:
  - `VALID_REASONS = frozenset(` / `{"refund_correction", "chargeback", "goodwill", "fee_waiver"}` at `services/settlement/payouts/adjustments.py:7-9`.
  - `if not validate_adjustment(adjustment):` / `continue` at `adjustments.py:29-30`.
  - `if entry is None:` / `continue` at `adjustments.py:32-33`.
  - `entry["gross_cents"] += adjustment["amount_cents"]` at `adjustments.py:34`.
  - PR code: `VALUES ($1, $2, 'refund', CURRENT_DATE)` at `refund.py:25`, and `seller = await conn.fetchrow(_SELLER_QUERY, order_id)` at `refund.py:59`.
- **Suggested fix**: Move this logic into settlement, as the finding "Checkout writes settlement tables directly" says. If it must stay in checkout for now, do these steps:
  - Write a negative amount.
  - Use a reason that settlement accepts. Agree on the reason with the settlement owners.
  - Take the amount for each seller from `settled_orders`, not from the buyer total.
  - Raise a mapped `CheckoutError` when no `settled_orders` row exists.
  - Add a test that sends a row from the refund path through `merge_adjustments`.
- **Effort**: Medium

### [High] The migration blocks order writes, or fails on the 5-second statement timeout
- **Category**: Performance
- **Location**: services/checkout/migrations/0008_add_refunds.py:22 (head f61de6b); also 0008_add_refunds.py:29-33
- **Problem**: The migration runs a plain `CREATE INDEX` on `orders` in the same transaction as `CREATE TABLE refunds`. A plain `CREATE INDEX` takes a lock that blocks every `INSERT` and `UPDATE` on the table until the build ends. Checkout writes about 800 orders per minute, which is about 1.15 million rows per day. So `orders` is a large table with many writes. The migration gets its connection from the dbkit pool. The pool sets `statement_timeout` to 5000 ms on every connection (`libs/dbkit/config.py:16`, `libs/dbkit/pool.py:45`).

  Two results are possible:
  - The build takes more than 5 s. Postgres stops it and rolls back the transaction. The `refunds` table is then not created, and every refund call fails.
  - An operator raises the timeout and runs the script again. Order placement then blocks for the full build time.

  No query in the PR or in the repo reads `orders` by `(status, updated_at)`. The index adds write cost to every order insert and every status update. The description says that the index is "for the refunds dashboard". But the index is on `orders`, not on `refunds`, and the repo has no dashboard code.
- **Evidence**: Sibling migration 0006 creates indexes only on tables that the same script creates (`services/checkout/migrations/0006_add_webhooks.py:22-25`). The only query on `orders.updated_at` selects one row by primary key (`services/checkout/orders/summary.py:8`).
- **Suggested fix**:
  - Delete `_CREATE_STATUS_INDEX` from this PR.
  - Add the index in the PR that adds the dashboard query.
  - Use `CREATE INDEX CONCURRENTLY` outside a transaction, with a higher timeout for that session.
  - Add `UNIQUE (order_id)` to `refunds`. A full-order refund occurs one time per order, and the foreign key column has no index now.
- **Effort**: Small

### [High] The `order.refunded` webhook can never reach a subscriber
- **Category**: Intent vs implementation
- **Location**: services/checkout/orders/refund.py:69 (head f61de6b)
- **Problem**: The PR sends the webhook to the topic `order.refunded`, but `KNOWN_TOPICS` does not contain it. `add_subscription` rejects an unknown topic with `ValidationFailure`, so no integrator can subscribe. `subscribers_for("order.refunded")` always returns an empty list, and `dispatch` always delivers zero events. The description says that integrators "see refunds the same way they see placements and cancellations". That claim is false.
- **Evidence**: `KNOWN_TOPICS` at `services/checkout/webhooks/registry.py:8` lists only `order.placed`, `order.reserved`, `order.settled`, and `order.cancelled`. The check `if topic not in KNOWN_TOPICS:` / `raise ValidationFailure(["unknown topic %s" % topic])` is at `registry.py:36-37`.
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that registers a subscription for the topic.
- **Effort**: Small

### [Medium] The gateway handler skips the response envelope and the upstream error mapping
- **Category**: Architectural fit
- **Location**: services/gateway/handlers/orders_proxy.py:60 (head f61de6b); also orders_proxy.py:7 and orders_proxy.py:58
- **Problem**: The handler returns `web.json_response(resp.json(), status=resp.status_code)`. This has three effects:
  1. The response has no security headers, for example `x-frame-options`, `strict-transport-security`, and `cache-control: no-store`. It also has no `{"status", "data"}` envelope. So clients get a different body shape on this one route.
  2. Checkout can return a 5xx with a body that is not JSON. An example is the aiohttp default text for an unhandled exception, such as the `TypeError` in the settlement finding. Then `resp.json()` raises, and the gateway returns its own 500, not the 502 `upstream_error` that the siblings return.
  3. A request with no body makes `await request.json()` raise, and the gateway returns 500. The siblings have the same parse step, but their bodies are required. Here `reason` is optional, so a client can send no body. The checkout handler has the same problem at `services/checkout/routes.py:71-73`. A body that is not an object also makes `payload.get` raise `AttributeError`.
- **Evidence**: `services/gateway/responses.py:3-4` says "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints." The siblings return `responses.envelope(...)` (`orders_proxy.py:29`, `orders_proxy.py:51`). They map `status_code >= 500` to `responses.failure("upstream_error", ..., 502)` (`orders_proxy.py:25-26`, `orders_proxy.py:49-50`). `get_order` maps 404 to `not_found` (`orders_proxy.py:47-48`).
- **Suggested fix**: Return through `responses.envelope` and `responses.failure`. Copy the 404 and 5xx checks from `get_order`. Delete the new `from aiohttp import web` import. Treat a missing body as `{}`, and return 400 for a body that is not an object.
- **Effort**: Small

### [Medium] The stream event comes from a new producer that never flushes, so the event arrives late or is lost
- **Category**: Simplicity & economy
- **Location**: services/checkout/orders/refund.py:68 (head f61de6b)
- **Problem**: No service in the repo calls `publish` today. Checkout publishes no stream event for placement or cancellation: `create.py:52` and `create.py:71` call only `dispatch`. So the PR makes checkout a Redis stream producer for the first time, and the claim "the same way they see placements and cancellations" is false for the stream.

  `publish` only adds the event to a buffer in the process. The library sends the buffer when it holds 200 events, or when code calls `flush`. Checkout never calls `flush`. Refunds occur "a few" times per minute, so an event can wait an hour or more, and a process restart loses it. When the 200th event arrives, `flush` runs a synchronous Redis pipeline on the aiohttp event loop. The event type `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES`. No consumer of this stream exists in the repo.
- **Evidence**:
  - `_buffer.append((stream, codec.encode(event)))` and `if len(_buffer) >= PUBLISH_FLUSH_COUNT:` at `libs/eventkit/publisher.py:32-34`.
  - `PUBLISH_FLUSH_COUNT = 200` at `libs/eventkit/config.py:10`.
  - Synchronous client: `redis.Redis.from_url(REDIS_URL)` at `publisher.py:24`.
  - `KNOWN_EVENT_TYPES` at `libs/eventkit/schema.py:29-44`. It spells the sibling type `checkout.order_canceled`.
- **Suggested fix**: Delete the `publish` call and the `libs.eventkit` import. Use only the webhook, as the siblings do. If a consumer needs the stream event, add it in a separate PR. That PR must call `flush` after each request or at shutdown, register the event type, and add the consumer.
- **Effort**: Small

### [Medium] A concurrent cancel can overwrite `refunded` after the refund commits
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:21 (head f61de6b); also services/checkout/orders/create.py:62-70 and services/checkout/orders/txn.py:16
- **Problem**: The row lock stops only a second refund. `cancel_order` reads the status with a plain `SELECT` that has no lock. Then it runs `_UPDATE_STATUS`, which has no status condition. This sequence can occur:
  1. Cancel reads `placed`.
  2. Refund locks the row, writes the refund row, sets `refunded`, and commits.
  3. Cancel releases inventory and sets `cancelled`.

  The order then shows `cancelled`, but a `refunds` row exists for it. Cancel and refund each run "a few times per minute", so this sequence is rare. But each time it occurs, the money record and the order status disagree. The description claims only that "two concurrent requests cannot both refund one order", and that narrow claim is true.
- **Evidence**: `_STATUS_QUERY = "SELECT status FROM orders WHERE id = $1"` at `create.py:29`. `_UPDATE_STATUS = "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1"` at `txn.py:16`.
- **Suggested fix**: Make status updates conditional on the expected status, for example `... WHERE id = $1 AND status = $3`. Check the row count in `update_status`. As an alternative, lock the row with `FOR UPDATE` in `cancel_order`.
- **Effort**: Small

### [Medium] Tests cover only the pure helpers, and one test asserts the wrong amount
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:26 (head f61de6b)
- **Problem**: The tests check only `refund_amount_cents` and `can_transition`. No test calls `refund_order`, the checkout route, the gateway handler, or the migration. So no test checks the lock, the refund insert, the adjustment row, the event, or the webhook topic. `test_sums_every_line_with_tax` asserts 3940 cents, which is not the amount the buyer paid (see the amount finding). No test uses a tax rate where `int()` and `round()` give different results. I could not run the new tests: the import of `libs.dbkit` needs `asyncpg`, and the review environment does not have it.
- **Evidence**: The repo already tests an async checkout function with a patched database call: `class OrderStatusViewTest(unittest.IsolatedAsyncioTestCase):` at `services/checkout/tests/test_summary.py:31`.
- **Suggested fix**:
  - Add async tests for `refund_order` with a fake connection.
  - Test an unknown order, a wrong status, a placed order, and a settled order with and without a `settled_orders` row.
  - Assert the SQL arguments, including the amount and the adjustment sign.
  - Add a test that `add_subscription("order.refunded", ...)` succeeds.
  - Add gateway tests for a 5xx from checkout and for a caller with no refund scope.
- **Effort**: Medium

### [Low] The status list and the service README do not show the new status and endpoint
- **Category**: Maintainability
- **Location**: services/checkout/config.py:22 (head f61de6b)
- **Problem**: `ORDER_STATUS_VALUES` still lists five statuses and does not include `refunded`. No code reads it today, so nothing breaks. But the constant now gives wrong information. The endpoint list in `services/checkout/README.md` does not include `POST /v1/orders/{order_id}/refund`.
- **Evidence**: `ORDER_STATUS_VALUES = ("pending", "reserved", "placed", "settled", "cancelled")` at `services/checkout/config.py:22`. The endpoint list starts at `services/checkout/README.md:25`.
- **Suggested fix**: Add `refunded` to the tuple, or delete the unused tuple. Add the endpoint to the README.
- **Effort**: Small

## Issues Not Found
- **API & interface design**: The route shape matches the sibling `/cancel` route (`services/checkout/routes.py:25`). The 201 status matches `create_order` (`routes.py:44`) for a new resource.
- **SQL injection**: All new SQL uses bound parameters (`$1`, `$2`, and so on). No code formats values into SQL text.
- **Sensitive data in logs**: The PR adds no log lines. The `reason` text goes only to the database.
- **Performance of the request path**: Refunds run a few times per minute. The transaction holds the row lock for a few short statements. The only performance issue is the migration index.
- **Refund against refund**: The `FOR UPDATE` lock inside `transaction()` correctly stops a double refund.
- **Webhook error handling**: `dispatch` logs failed deliveries and does not raise. This matches the sibling calls in `create.py`.
- **Scope & PR hygiene**: The PR does one feature. The only small edit outside the feature is the gateway module docstring, which describes the change.
- **Reversibility**: You can drop the table and the index. After a code rollback, the old `can_transition` treats `refunded` as a status with no next status (`_NO_NEXT` at `services/checkout/orders/models.py:51`). So old code does not crash on those rows.

## Open Questions
1. **Who can ask for a refund?** Buyers, support staff, and sellers need different scopes. Buyers on settled orders may need an approval step. The product spec, or the list of auth scopes, answers this question.
2. **What moves the money to the buyer?** The repo has no client for a payment provider. The PR only records the refund. If no external process reads `refunds` and pays the buyer, the buyer gets no money. The name of the process that reads the `refunds` table answers this question.
3. **Must a refund of a `placed` order release the inventory hold?** `cancel_order` calls `release_order` for a placed order (`services/checkout/orders/create.py:69`), but `refund_order` does not. The answer depends on whether a placed order has shipped. The product owner can answer this question.
4. **Can one order have rows for more than one seller in `settled_orders`?** The answer controls the seller split of the adjustment. The schema of `settled_orders`, or the code that writes it, answers this question. That code is not in this repo.
5. **When does an order become `settled`, compared with the settlement payout?** If settlement pays a seller for an order before checkout marks it `settled`, a refund of that `placed` order writes no adjustment. The seller then keeps the payment. The code that writes `settled_orders` and sets the `settled` status answers this question. That code is not in this repo.
6. **Which time zone does the database use for `CURRENT_DATE`?** Settlement selects adjustments by `previous_utc_day()` (`services/settlement/windows.py:8-11`). If the database time zone is not UTC, a row written near midnight goes to the wrong day. The `TimeZone` setting of the database answers this question.