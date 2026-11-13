# PR Independent Review

## PR
acme/bazaar#1 — checkout: full-order refunds
https://github.com/acme/bazaar/pull/1
7 files changed, +179/-4 lines, head f61de6b

## What this PR does (from the diff)
The PR adds `POST /v1/orders/{order_id}/refund` to checkout, and the gateway passes the route through unchanged. In one transaction, the checkout function locks the order row, calculates an amount from `items_json`, writes a `refunds` row, and sets the status to `refunded`. For a settled order, it also writes a `payout_adjustments` row. After the commit, it adds a `checkout.order_refunded` event to the stream buffer and sends an `order.refunded` webhook. Migration 0008 adds the `refunds` table and an index on `orders (status, updated_at)`.

## What the author says it does
The author says that placed and settled orders can get a full refund through the gateway, and that the amount is "the full amount the buyer was charged for the order". The author also makes these claims:
- The row lock stops two concurrent refunds.
- The seller's payout "is corrected in the next settlement run".
- Downstream services and integrators see refunds "the same way they see placements and cancellations".

## The underlying problem
A buyer or a support agent needs to return the money for a placed or settled order. The order must show as refunded. For a settled order, the next payout to the seller must go down by the amount that the seller got for that order.

My minimal sketch changes about 6 files:
- the status table
- one refund function next to `cancel_order` that refunds the stored `total_cents`
- one new topic in `KNOWN_TOPICS`
- the two route tables, plus a gateway handler that follows the shape of `place_order`
- a migration for the `refunds` table

The PR is close to that size. But it adds three parts that the sketch does not need: the `orders` index, the stream event, and `refund_amount_cents`. It also leaves out two parts that the sketch needs: the topic registration and the gateway checks.

## Summary
The PR has a good shape, and the row lock does stop two concurrent refunds of one order. But three claims in the description are false in the code. The seller payout is never corrected, the refund amount is not the charged amount, and no integrator or service gets the refund events. The most important finding is the payout adjustment. Settlement drops the row, and if settlement applied it, the positive amount would pay the seller a second time.

## Findings

### [Critical] Settlement drops the seller payout adjustment, and the adjustment has the wrong sign
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:23-26, :59-60 (head f61de6b)
- **Problem**: The refund writes `reason = 'refund'` and a positive `amount_cents`. Settlement accepts only the reasons in `VALID_REASONS` (`services/settlement/payouts/adjustments.py:7-9`), and `'refund'` is not in that set. `merge_adjustments` skips the row and records no error (`adjustments.py:29-30`). Thus the buyer gets the money back, the seller keeps the full payout, and the platform pays the loss on each settled refund. This contradicts the description: "the seller's payout is corrected in the next settlement run".

  A fix to the reason alone is not enough. `merge_adjustments` adds `amount_cents` to the seller gross (`adjustments.py:34`), so a positive amount pays the seller a second time. The amount is also the wrong figure, because it is the buyer amount, which includes tax. Settlement paid the seller from `gross_cents` and `net_cents` in `settled_orders` (`services/settlement/cron.py:29-32`).

  Other effects:
  - If `settled_orders` has no row for the order, `seller` is `None`, and `seller["seller_id"]` raises `TypeError` at `refund.py:60`. The transaction rolls back, and the client gets a 500. A settled order with no such row can never get a refund.
  - If one order has more than one `settled_orders` row, `fetchrow` adjusts only the first seller.
  - `merge_adjustments` skips an adjustment when the seller has no settled orders on that day (`adjustments.py:31-33`). This behavior is older than the PR, but the clawback depends on it. A seller with no sales on the refund day keeps the money.
- **Evidence**: I ran `merge_adjustments` from the repo on a seller gross of 50000 with the PR row `{'seller_id': 'sel_1', 'amount_cents': 4539, 'reason': 'refund'}`. It applied 0 rows, and the gross stayed at 50000. With `reason='refund_correction'` and the same positive amount, it applied 1 row, and the gross went up to 54539.
  ```
  _INSERT_ADJUSTMENT = """
      INSERT INTO payout_adjustments (seller_id, amount_cents, reason, day)
      VALUES ($1, $2, 'refund', CURRENT_DATE)
  """
  ```
- **Suggested fix**: Use a reason that settlement accepts, for example `refund_correction`. Write a negative amount. Take the amount for each seller from `settled_orders`, not from the buyer total. Raise a clear error when no row exists. Write one adjustment for each seller row. Add a test that sends the written row through `merge_adjustments`. The Architectural fit finding below gives a better location for this logic.
- **Effort**: Medium

### [High] Migration 0008 locks the busy `orders` table to build an index that no code uses
- **Category**: Simplicity & economy
- **Location**: services/checkout/migrations/0008_add_refunds.py:22-24, :31-33 (head f61de6b)
- **Problem**: The migration runs a plain `CREATE INDEX` on `orders` in the same transaction that creates `refunds`. A plain `CREATE INDEX` takes a `SHARE` lock. That lock stops every `INSERT` and `UPDATE` on `orders` until the build ends. Checkout writes about 800 orders per minute, about 13 per second at peak (`services/checkout/README.md:7`). Thus `orders` gets about 1.15M new rows each day, and an index build on it is slow.

  The migration gets its connection from `libs.dbkit.pool.get_pool`, which sets `statement_timeout` to 5000 ms on every connection (`libs/dbkit/config.py:16`, `libs/dbkit/pool.py:43-46`). Two results follow:
  - For up to 5 s on each try, every order write waits on the lock. At 13 placements per second, about 65 requests wait. Each waiting request holds a connection from a pool of 20 (`libs/dbkit/config.py:13`), so status reads in that process also wait.
  - If the build takes more than 5 s, Postgres stops it, and the transaction rolls back. The `refunds` table then does not exist, and every refund call fails with a 500.

  The PR body says the index is "for the refunds dashboard". No code in this PR reads `orders` by `(status, updated_at)`. The refund path reads by primary key. The index also adds work to each order insert and to each status update.
- **Evidence**:
  ```
  CREATE INDEX orders_status_updated_idx ON orders (status, updated_at)
  ```
  The precedent `services/checkout/migrations/0006_add_webhooks.py:22-25` also creates an index in a transaction. But that index is on a new, empty table, so it has no lock cost.
- **Suggested fix**: Remove the index from this PR. Add it in the PR that adds the dashboard query. In that PR, use `CREATE INDEX CONCURRENTLY` outside a transaction. Run it on a connection that does not have the 5 s statement timeout.
- **Effort**: Small

### [High] The refund endpoint has no ownership, scope, or audit check
- **Category**: Security
- **Location**: services/gateway/handlers/orders_proxy.py:55-60; services/checkout/orders/refund.py:14-16 (head f61de6b)
- **Problem**: The gateway handler forwards a refund for any client that sends a bearer token. The auth middleware only checks for the token and attaches a session (`services/gateway/middleware/auth.py:36-41`). The handler does not read the session or call `require_scope`. It does not send `x-tenant-id`, and it writes no audit record. In checkout, `_LOCK_ORDER` selects `buyer_id`, but no code compares it with the caller.

  As a result, any client of any tenant can refund any order if it knows the order ID. The buyer of a settled order can also refund it at any time, with no approval step. Each refund writes money records: a `refunds` row and a seller adjustment. The gateway keeps no record of the caller. This is new attack surface on a write path that moves money.
- **Evidence**: The sibling write handler `place_order` checks the session, sends `x-tenant-id`, and writes an audit record (`services/gateway/handlers/orders_proxy.py:19-21`, `:23-25`, `:28-29` at head). The docstring of `require_scope` says "Called by handlers that guard writes." (`services/gateway/middleware/auth.py:25`). `tenants.get_tenant` calls `require_scope` (`services/gateway/handlers/tenants.py:26`).
- **Suggested fix**: Read the session in the handler. Return 401 when there is no session. Call `require_scope` with a refund scope. Send `x-tenant-id` to checkout. In checkout, refuse the refund when the caller is not the buyer or a support role. Write an audit record with `action="order.refund"`, as `place_order` does.
- **Effort**: Medium

### [High] The refund amount is not the amount that the buyer paid
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:29-35, :52 (head f61de6b)
- **Problem**: The buyer pays `orders.total_cents`. Placement calculates it like this: subtotal, plus tax rounded on each line, plus shipping, minus the promo discount (`services/checkout/orders/create.py:75-80`, `services/checkout/carts/totals.py:27-45`, `services/checkout/promotions.py:31-33`). `refund_amount_cents` calculates only the subtotal plus tax. It omits shipping and ignores the promo. It also uses `int()` truncation, but placement uses `round()`. This contradicts the description: "The refund amount is the full amount the buyer was charged".

  I calculated these results:
  - The test items in region `us`: the buyer paid 4539 cents (3700 + 240 tax + 599 shipping). The refund is 3940 cents, which is 599 cents too little.
  - One 10000-cent line at 8 percent tax with a 20 percent promo: the buyer paid 8640 cents. The refund is 10800 cents, which is 2160 cents too much.
  - One 1999-cent line at 8.25 percent tax: the buyer paid 2164 cents, and the refund is 2163 cents. In a sample of 12735 price and tax-rate pairs, 6041 pairs gave a difference of 1 cent.

  The function also reads `items_json` while it holds the row lock. That column is about 25 KB for each order (`services/checkout/orders/models.py:3-4`). The row already stores the correct value.
- **Evidence**:
  ```
  total += int(line_cents * (1 + entry["tax_rate"]))
  ```
  `_LOCK_ORDER` selects `items_json` but not `total_cents`.
- **Suggested fix**: Select `total_cents` in `_LOCK_ORDER`. Refund that value. Delete `refund_amount_cents`. Remove `items_json` from the query.
- **Effort**: Small

### [High] No integrator can subscribe to the `order.refunded` webhook
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:69 (head f61de6b)
- **Problem**: `dispatch("order.refunded", ...)` sends the event to the subscribers of that topic. `add_subscription` rejects every topic that is not in `KNOWN_TOPICS` (`services/checkout/webhooks/registry.py:36-37`), and `KNOWN_TOPICS` does not include `order.refunded` (`registry.py:8-15`). Thus no subscription for the topic can exist, and `subscribers_for` always returns an empty list. No integrator gets a refund event. This contradicts the description, which says integrators see refunds "the same way they see placements and cancellations".
- **Evidence**: A `POST /v1/webhooks` with `{"topic": "order.refunded", ...}` gets a 400 with "unknown topic order.refunded" from `registry.py:37`.
- **Suggested fix**: Add `"order.refunded"` to `KNOWN_TOPICS`. Add a test that registers the topic.
- **Effort**: Small

### [Medium] Checkout reads and writes tables that settlement owns
- **Category**: Architectural fit
- **Location**: services/checkout/orders/refund.py:22-26, :56-60 (head f61de6b)
- **Problem**: Before this PR, no checkout code used `settled_orders` or `payout_adjustments`. Only settlement (`services/settlement/cron.py:29-37`) and the ledger tool (`tools/verify_ledger.py:22-26`) read them. The rules for an adjustment row live in `services/settlement/payouts/adjustments.py:7-35`. Its docstring says "Adjustment rows come from the support tooling table". The PR copies that contract into checkout as raw SQL and gets two rules wrong: the reason and the sign (see the Critical finding). Each later change to the settlement rules must also change checkout, and no check will show a mismatch.
- **Evidence**: `_SELLER_QUERY = "SELECT seller_id FROM settled_orders WHERE order_id = $1"` and `_INSERT_ADJUSTMENT` in `refund.py`. Compare `ADJUSTMENTS_QUERY` in `services/settlement/cron.py:34-37`.
- **Suggested fix**: Let checkout write only its own tables. Make the nightly settlement run read the `refunds` rows for settled orders and do the clawback itself. Settlement already reads the same database.
- **Effort**: Medium

### [Medium] The gateway refund handler does not use the response envelope or the upstream error mapping
- **Category**: Architectural fit
- **Location**: services/gateway/handlers/orders_proxy.py:7, :55-60 (head f61de6b)
- **Problem**: The handler returns `web.json_response(resp.json(), status=resp.status_code)`. But `services/gateway/responses.py:3-4` says "Every handler and middleware returns through these helpers so bodies and security headers stay uniform across endpoints." The direct response has these effects:
  - The refund response does not have the `x-frame-options`, `strict-transport-security`, or `cache-control: no-store` headers (`responses.py:10-17`).
  - The body has the raw checkout shape (`{"error": ...}` or the event), not `{"status", "data"}`. Clients must parse this endpoint in a different way.
  - A checkout 5xx goes to the client unchanged. If the 500 body is not JSON, `resp.json()` raises and the gateway returns an unhandled 500. One example is the `TypeError` in the Critical finding.

  The PR adds the `from aiohttp import web` import only for this line.
- **Evidence**: The sibling handlers `list_orders` and `get_order` map `>= 500` to `responses.failure("upstream_error", ..., 502)`, and they return `responses.envelope(...)` (`orders_proxy.py:39-41`, `:50-52` at head).
- **Suggested fix**: Copy the shape of `place_order`. Map `>= 500` to `responses.failure(..., 502)`. Return `responses.envelope(resp.json(), status=resp.status_code)`. Remove the `aiohttp` import.
- **Effort**: Small

### [Medium] The stream event has no reader, no flush, and no registered type
- **Category**: Simplicity & economy
- **Location**: services/checkout/orders/refund.py:7, :68 (head f61de6b)
- **Problem**: This is the first `publish` call in checkout. `publish` only adds the event to a buffer in the process. The buffer goes to Redis when it holds `PUBLISH_FLUSH_COUNT = 200` events (`libs/eventkit/config.py:10`, `libs/eventkit/publisher.py:28-34`). Checkout never calls `flush`. At 3 refunds per minute, the first event waits about 67 minutes, and a deploy or restart deletes the buffer. When the 200th event arrives, `flush` runs a sync `redis.Redis` pipeline on the checkout event loop (`publisher.py:20-25`, `:41-50`). Order placement stops for that round trip.

  Other problems:
  - `checkout.order_refunded` is not in `KNOWN_EVENT_TYPES` (`libs/eventkit/schema.py:29-44`).
  - No code reads `bazaar:checkout:order`. The only caller of `consume` is `services/pricing/consumer/loop.py:40`.
  - Placements and cancellations do not use the stream. They use only `dispatch` (`services/checkout/orders/create.py:52`, `:71`). Thus the description is wrong when it says the stream event lets services see refunds "the same way they see placements and cancellations".
- **Evidence**: `publish(stream_name("checkout", "order"), make_envelope("checkout.order_refunded", event))`
- **Suggested fix**: Delete the `publish` call and its import. If a service needs a checkout order stream, add it in a separate PR for all order events. That PR must register the types, flush at the end of each request, and add the consumer.
- **Effort**: Small

### [Medium] A concurrent cancel can overwrite a refund
- **Category**: Correctness
- **Location**: services/checkout/orders/refund.py:44-55 (head f61de6b); services/checkout/orders/create.py:62-70, services/checkout/orders/txn.py:16 (unchanged by the PR)
- **Problem**: The row lock in `refund_order` only stops a second refund. `cancel_order` reads the status with no lock and then calls inventory over HTTP. Then it runs an `UPDATE` with no status condition. This sequence is possible:
  1. Cancel reads the status `placed`, and the check passes.
  2. Refund locks the row, writes the `refunds` row, sets `refunded`, and commits.
  3. Cancel releases the stock and sets `cancelled`.

  The order is then `cancelled`, but it has a `refunds` row. Both `order.cancelled` and `order.refunded` go out. The inventory call in step 3 has a timeout of 10 s (`services/checkout/config.py:28`), so the window can be long. The unlocked cancel path is older than the PR. But the PR adds a second writer that records money. The description also says the lock stops concurrent refunds, but it does not say that a cancel can still overwrite one.
- **Evidence**: `_UPDATE_STATUS = "UPDATE orders SET status = $2, updated_at = now() WHERE id = $1"` (`txn.py:16`).
- **Suggested fix**: Give `update_status` the expected old status. Add `AND status = $3` to the `UPDATE`. Fail the cancel when no row changes. Another option: lock the row in `cancel_order` in the same way as the refund.
- **Effort**: Small

### [Medium] The tests assert the wrong amount and do not cover `refund_order`
- **Category**: Testing
- **Location**: services/checkout/tests/test_refund.py:25-26 (head f61de6b)
- **Problem**: `test_sums_every_line_with_tax` asserts `3240 + 700`. For these items in region `us`, the buyer paid 4539 cents, so the test locks in the defect from the refund amount finding. No test runs `refund_order`, so these parts have no test: the lock, the status move, the `refunds` row, the adjustment row, and the events. A test that sends the adjustment row through `merge_adjustments` would have found the Critical finding. No test covers the gateway handler.
- **Evidence**: `self.assertEqual(refund_amount_cents(_ITEMS), 3240 + 700)`
- **Suggested fix**: Delete the amount tests when you delete `refund_amount_cents`. Add `refund_order` tests for three cases: a placed order, a settled order, and a status that is not allowed. For the settled order, check the reason and the sign of the adjustment. `tests/helpers/fakes.py:11-61` has `FakeConn` and `FakePool`, which you can extend for this path. Add a gateway test for the session and scope checks.
- **Effort**: Medium

### [Low] A refund request with no body returns 500
- **Category**: Error handling
- **Location**: services/checkout/routes.py:71-73; services/gateway/handlers/orders_proxy.py:58 (head f61de6b)
- **Problem**: The `reason` field is optional, but both handlers call `await request.json()` with no guard. A `POST` with an empty body raises `JSONDecodeError`, and the client gets a 500. A JSON body that is not an object (for example `[]`) makes `payload.get` raise `AttributeError` in checkout, which also gives a 500. The sibling action `cancel_order` reads no body (`services/checkout/routes.py:58-65` at head).
- **Evidence**: `payload = await request.json()` followed by `payload.get("reason", "")`.
- **Suggested fix**: Read the body only when `request.can_read_body` is true. Return 400 when the body is not a JSON object.
- **Effort**: Small

## Issues Not Found
I found no issues in these categories: API & interface design, Best practices & idioms (except the sync flush above), Performance (except the index and the flush above), Observability, Maintainability, and Scope & PR hygiene. The lock claim is correct for two refunds of one order. With `FOR UPDATE` in read-committed mode, the second request waits, reads the row again, and fails the `refunded` check.

## Open Questions
1. **What sends the money back to the buyer?** The repo has no payment code. A search for "payment", "charge", and "capture" found nothing. The answer would show which part (the `refunds` table, the stream, or the webhook) a payment system outside the repo reads. That answer can change the severity of the webhook and stream findings.
2. **Must a refund of a `placed` order release the stock?** `cancel_order` calls `release_order` (`services/checkout/orders/create.py:69`), but `refund_order` does not. If `placed` means "not shipped", the stock stays held after a refund. A product owner must also say how cancel and refund differ for a placed order.
3. **Who writes the `settled` status and the `settled_orders` rows?** No code in this repo does this. If that writer does not lock the row or check the status, it can move a `refunded` order to `settled` and pay the seller.
4. **Can one order have more than one `settled_orders` row, one for each seller?** If yes, the clawback must cover each seller row.
5. **What is the Postgres `TimeZone` for the checkout connections?** The adjustment uses `CURRENT_DATE`. Settlement runs at 02:00 UTC (`services/settlement/config.py:9`) and reads `day = previous_utc_day()`. Suppose the database zone is UTC-5. Then a refund between 02:00 and 05:00 UTC gets the date of a day that settlement already processed, and settlement never reads it.
6. **Who must be able to call the endpoint?** "Exposed to clients through the gateway" can mean buyers or support staff only. The answer decides the scope and ownership rule in the Security finding.