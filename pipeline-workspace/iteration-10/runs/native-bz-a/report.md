I reviewed PR #1, "checkout: full-order refunds", which is `origin/order-refunds` compared with `main`. I found 10 issues. The most serious are that sellers never get debited for a refund, that anyone can refund any order through the gateway, and that the refund amount doesn't match what the buyer paid. The only CLAUDE.md that applies is `~/.claude/CLAUDE.md`, and I found no clear violation of it. No verification pass ran (recall mode, as requested).

```json
[
  {
    "file": "services/checkout/orders/refund.py",
    "line": 25,
    "summary": "The payout adjustment row uses reason 'refund' and a positive amount_cents. settlement/payouts/adjustments.py validate_adjustment only accepts {refund_correction, chargeback, goodwill, fee_waiver}, so the nightly run silently skips the row. Even if the reason were accepted, merge_adjustments does gross_cents += amount_cents, so a positive amount pays the seller more instead of clawing money back.",
    "failure_scenario": "A settled order for 5000 cents is refunded. The buyer gets 5000 back, but settlement drops the 'refund' row, so the seller keeps the 5000 they were already paid and the marketplace loses that money on every refund of a settled order. If the reason is renamed to refund_correction without flipping the sign, the seller is credited another 5000."
  },
  {
    "file": "services/gateway/handlers/orders_proxy.py",
    "line": 55,
    "summary": "The gateway refund_order handler never checks request.get('session'), never checks that the caller owns the order or tenant, and sends no x-tenant-id header. Every other mutating order proxy returns 401 when there is no session.",
    "failure_scenario": "An unauthenticated client sends POST /v1/orders/ord_<any id>/refund and the gateway passes it to checkout, which refunds the order. Anyone who knows or guesses an order id can refund another buyer's order or trigger seller claw-backs."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 34,
    "summary": "refund_amount_cents rebuilds the amount from items_json. It truncates each line with int() instead of round(), leaves out shipping, and ignores promo discounts. The buyer was actually charged orders.total_cents, which is grand_total_cents (round per line, plus shipping) minus any promo.",
    "failure_scenario": "A us order of 2000 cents at 8% tax with 599 shipping charges 2759 but refunds only 2160, so the buyer is short 599. An order placed with a 50%-off promo refunds the full undiscounted price, so the buyer gets more back than they paid. A line of 1999 at 7.25% tax (2143.93) truncates to 2143, while the charge rounded to 2144."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 68,
    "summary": "eventkit.publish only adds the event to a buffer, and nothing in checkout ever calls flush(). The buffer is flushed only after PUBLISH_FLUSH_COUNT=200 events, and checkout publishes nothing else.",
    "failure_scenario": "At a few refunds per minute, checkout.order_refunded events sit in memory for an hour or more before any consumer sees them. A deploy or restart drops the whole buffer, so downstream services never learn about those refunds. When the 200th event does arrive, a synchronous Redis pipeline call blocks the aiohttp event loop."
  },
  {
    "file": "services/checkout/migrations/0008_add_refunds.py",
    "line": 23,
    "summary": "The migration runs CREATE INDEX (not CONCURRENTLY) on the hot orders table inside a transaction. That takes a SHARE lock that blocks all INSERT/UPDATE on orders until the index is built.",
    "failure_scenario": "During the 2.12 release, building orders_status_updated_idx over the full orders table (about 25 KB per row, 800 orders/min) blocks order placement, cancels and status updates for the whole build. Checkout times out and drops orders. The index also isn't used by any code in this PR."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 59,
    "summary": "The seller lookup uses fetchrow on settled_orders by order_id, which returns one arbitrary row and returns None when no row exists.",
    "failure_scenario": "A multi-seller order has several settled_orders rows. Only one seller is charged an adjustment, and it is for the full buyer amount including tax rather than that seller's net_cents. If an order is in 'settled' status but has no settled_orders row, seller['seller_id'] raises TypeError: the transaction rolls back and the client gets an unmapped 500."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 55,
    "summary": "Refunding a 'placed' order moves it to refunded without calling inventory release_order. cancel_order does release the stock holds for the same status.",
    "failure_scenario": "A placed (not yet settled) order is refunded. The inventory reservations for its items are never released, so that stock stays held and can't be sold."
  },
  {
    "file": "services/gateway/handlers/orders_proxy.py",
    "line": 60,
    "summary": "The proxy returns the upstream response as-is with web.json_response(resp.json()). It skips the 5xx-to-502 mapping, the responses.envelope wrapping, and the write_audit call that the other order proxies use.",
    "failure_scenario": "Checkout returns a 500 HTML or plain-text error, resp.json() raises, and the gateway crashes with an unhandled exception instead of returning 502. Successful refunds come back in a different response shape from other order endpoints, and a money-moving action leaves no audit record."
  },
  {
    "file": "services/checkout/routes.py",
    "line": 72,
    "summary": "The handler calls request.json() and payload.get(...) outside the try block and doesn't check the body, even though reason is optional.",
    "failure_scenario": "A POST /refund with an empty body raises JSONDecodeError, and a body like [] or \"x\" raises AttributeError on .get. Both return an unhandled 500 instead of a 400, or instead of refunding with an empty reason."
  },
  {
    "file": "services/checkout/orders/refund.py",
    "line": 15,
    "summary": "The FOR UPDATE query pulls items_json (about 25 KB per order) and re-derives the total with new money math, when orders.total_cents already holds the charged amount and carts/totals.py already has the shared rounding logic.",
    "failure_scenario": "Each refund transfers and parses about 25 KB while holding the row lock, and adds a second money formula that has already drifted from grand_total_cents. Selecting total_cents instead removes the duplicated math and the payload."
  }
]
```