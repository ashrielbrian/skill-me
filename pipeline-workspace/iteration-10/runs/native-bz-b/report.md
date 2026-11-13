Reviewed PR #1 (`order-refunds`, full-order refunds) against `main` at high effort. All 10 findings below are correctness bugs. Two smaller issues missed the cut:
- **Empty request body crashes:** `routes.py:71` calls `await request.json()` unguarded, so a refund request with no body returns a 500, even though `reason` should be optional.
- **Refund work duplicates existing totals code:** `refund_amount_cents` re-implements the tax math in `carts/totals.py` (`tax_cents`), and `orders.total_cents` already holds the amount to refund.

No CLAUDE.md rule was clearly broken.

```json
[
  {
    "file": "services/checkout/orders/refund.py",
    "line": 25,
    "summary": "The payout adjustment is written with reason 'refund', but settlement's `validate_adjustment` only accepts {'refund_correction','chargeback','goodwill','fee_waiver'}, so every refund adjustment is silently skipped.",
    "failure_scenario": "Refund a settled order: a row (seller_id, amount, 'refund', today) goes into payout_adjustments. The next night `merge_adjustments` sees `validate_adjustment` return False for it and `continue`s. The seller keeps the full payout for an order the buyer got back, so the platform pays the refund twice."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 34,
    "summary": "The refund amount is rebuilt from items_json as sum(int(price*qty*(1+tax))). It leaves out shipping and promo discounts and truncates tax where checkout rounds it, so it is not the amount the buyer was charged (orders.total_cents).",
    "failure_scenario": "(a) A $20 order to region 'us' paid 599 cents shipping, which is never refunded. (b) An order with a 20% promo code is over-refunded by the full discount. (c) A line of 1499 cents at tax 0.0725 has tax 108.68: checkout rounds it to 109, the refund truncates to 108 (1607 vs 1608). Reading total_cents from the locked row fixes all three."
  },
  {
    "file": "services/gateway/handlers/orders_proxy.py",
    "line": 55,
    "summary": "The gateway refund handler never checks the session's scope or that the caller owns the order (it doesn't use `require_scope`, sends no x-tenant-id, writes no audit record), so any caller with a session token can refund any order by ID.",
    "failure_scenario": "Any logged-in buyer or tenant sends POST /v1/orders/<someone else's ord_id>/refund. The auth middleware only checks that a bearer token is present, checkout does no ownership check, and the victim's order is refunded with no audit trail."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 68,
    "summary": "eventkit's `publish` only adds the event to a buffer that flushes at 200 entries, and checkout never calls `flush()`, so `checkout.order_refunded` can sit in process memory for hours and is lost on restart. The event type is also not in eventkit's KNOWN_EVENT_TYPES.",
    "failure_scenario": "At a few refunds per minute, the buffer reaches 200 only after about an hour or more. A deploy or crash before that drops every buffered refund event, so downstream consumers never see them. Consumers that gate on `is_known_type` reject the event even when it does arrive."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 69,
    "summary": "The PR dispatches the 'order.refunded' webhook, but `registry.KNOWN_TOPICS` does not include it, so `add_subscription` rejects the topic and nobody can ever subscribe.",
    "failure_scenario": "An integrator POSTs /v1/webhooks with topic 'order.refunded' and gets 400 'unknown topic'. `dispatch` always finds zero subscribers, so the webhook the PR promises is never delivered."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 60,
    "summary": "The adjustment is inserted with a positive amount_cents, and `merge_adjustments` adds it to gross_cents, so once the reason is fixed the seller's payout goes up instead of being clawed back.",
    "failure_scenario": "Refund a settled 5000-cent order. After fixing the reason, settlement does `gross_cents += 5000`, and the seller is paid the order amount a second time instead of having it deducted. The amount should be inserted as -amount_cents."
  },
  {
    "file": "services/checkout/migrations/0008_add_refunds.py",
    "line": 23,
    "summary": "`CREATE INDEX` on the busy orders table runs without CONCURRENTLY, inside a transaction, so it holds a SHARE lock that blocks every insert and update on orders until the index is built.",
    "failure_scenario": "Running 0008 in production during the 2.12 release blocks order placement (about 13/s) and status updates for the whole index build on a large orders table. Checkout requests pile up and time out. Use CREATE INDEX CONCURRENTLY outside the transaction."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 56,
    "summary": "Refunding a 'placed' order moves it to a terminal state without releasing its inventory holds, unlike `cancel_order`, which calls `release_order`.",
    "failure_scenario": "A buyer refunds a placed but not yet settled order. Its reserved stock stays held for good (or until reservation GC runs), so other buyers see the products as out of stock."
  },
  {
    "file": "services/gateway/handlers/orders_proxy.py",
    "line": 60,
    "summary": "The gateway refund handler returns checkout's body raw through `web.json_response` and skips the 5xx → 502 mapping, so it bypasses `responses.envelope` (no security headers, different body shape) and crashes when the upstream body is not JSON.",
    "failure_scenario": "If checkout returns an HTML or plain-text 500 or 502, `resp.json()` raises JSONDecodeError and the gateway returns an unhandled 500. Successful responses come back without the {status,data} envelope and the security headers, unlike every other gateway endpoint."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 59,
    "summary": "The settled branch assumes exactly one settled_orders row per order. `fetchrow` returns None when there is no row (TypeError) and only the first row when the order has several sellers, and that seller is charged the full order amount.",
    "failure_scenario": "(a) The order is 'settled' but its settled_orders row is missing or not written yet: `seller['seller_id']` raises TypeError, the transaction rolls back, and the client gets a 500. (b) A two-seller order gets one adjustment for the whole amount against an arbitrary seller, and the other seller keeps their payout."
  }
]
```