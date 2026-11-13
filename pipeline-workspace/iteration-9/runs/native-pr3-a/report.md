PR #3 (`bulk` → `main`) looks unsafe to merge as written. The new retry loop in `CarrierClient._post` also covers `create_label`, which can buy duplicate labels. The bulk endpoint also has gaps in its all-or-nothing guarantee. No `CLAUDE.md` rule is clearly broken by the diff. The `~/.claude/CLAUDE.md` comment rule doesn't catch the new transaction comment, because that comment explains a design choice.

```json
[
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 25,
    "summary": "The new retry loop in `_post` applies to every carrier call, including the non-idempotent `create_label`. This undoes the explicit 'Not retried' safeguard in `services/shipments.py:20-22`, whose comment is now wrong.",
    "failure_scenario": "The `labels` POST reaches the carrier, which buys the label, but the reply is an HTTP 502/504 or a URLError-wrapped socket error. `_post` sends the same request again (CARRIER_RETRIES defaults to 3), so up to 3 labels are bought and paid for while only one is stored."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 45,
    "summary": "Bulk 'all-or-nothing' only rolls back the database rows. Labels already bought at the carrier for earlier items are never voided.",
    "failure_scenario": "Items 1 and 2 succeed (two labels bought) and item 3's `get_rate` raises UpstreamError. The transaction rolls back and the client gets a 502, but LBL-1 and LBL-2 stay purchased with no shipment row pointing to them, so they are billed and orphaned. The client retries the batch and buys them again."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 46,
    "summary": "The bulk path inserts shipments without the 'labelled' event. That breaks the rule stated in `create_shipment` that a shipment never exists without its 'labelled' event.",
    "failure_scenario": "`POST /shipments/bulk` with 2 items creates 2 shipments rows and 0 `shipment_events` rows. Any event history, timeline or refund logic that expects the first 'labelled' event will see none or break for every bulk-created shipment."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 40,
    "summary": "Carrier network calls and retry sleeps run inside `BEGIN IMMEDIATE` on the single shared sqlite connection (`check_same_thread=False`). The write lock or open transaction is held for the whole batch.",
    "failure_scenario": "A 50-item batch with 5s timeouts plus retry sleeps holds the transaction for minutes. A concurrent `POST /shipments` on the same connection calls `BEGIN IMMEDIATE` and fails with 'cannot start a transaction within a transaction' (500). Other threads' plain writes run inside the bulk transaction and are silently rolled back if the batch fails."
  },
  {
    "file": "parcelpost/services/quotes.py",
    "line": 8,
    "summary": "`with_retry` stays around `get_rate` at `quotes.py:8`, `shipments.py:19` and the new `shipments.py:42`, while `_post` now retries internally too. The attempts multiply to 3x3 = 9.",
    "failure_scenario": "The carrier is down with a 5s timeout. A quote request makes 9 HTTP attempts: 9×5s of timeouts plus 3×(0.5+1.0)s inner sleeps plus outer jittered sleeps, so about 50s before the 502. In bulk this happens per item while the DB transaction is open."
  },
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 29,
    "summary": "`except urllib.error.URLError` also catches `HTTPError` (a subclass), so permanent 4xx responses are retried with backoff along with transient network errors.",
    "failure_scenario": "The carrier returns 400 for an unsupported destination. The client sleeps 0.5s+1.0s and resends the same bad request twice before raising UpstreamError. Combined with the outer `with_retry`, one invalid quote causes 9 identical 400s and several seconds of latency."
  },
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 34,
    "summary": "If `retries` is 0 or negative, the loop never runs, `last` stays None, and `last.reason` raises AttributeError.",
    "failure_scenario": "An operator sets `CARRIER_RETRIES=0` meaning 'no retries'. Every carrier call raises AttributeError instead of making one request, so every quote and shipment returns a 500 'internal error'. The value is never validated in `config.py`."
  },
  {
    "file": "parcelpost/handlers/shipments.py",
    "line": 35,
    "summary": "Bulk items default `declared_value` to 0 and pass an explicit null to `to_cents`. Single create stores None when the value is absent and accepts null. This contradicts the PR's claim of 'same validation'.",
    "failure_scenario": "An item without `declared_value` is stored with `declared_cents=0` (declared as worth $0) instead of NULL. An item with `\"declared_value\": null`, which `POST /shipments` accepts, is rejected with a 422 'declared_value is not a valid amount'."
  },
  {
    "file": "parcelpost/handlers/shipments.py",
    "line": 28,
    "summary": "`items` is never checked to be a non-empty list of objects, and batch size has no cap.",
    "failure_scenario": "`\"items\": \"ab\"` iterates characters and `require_fields('a', ...)` raises AttributeError (500). `\"items\": [1]` also returns a 500. `\"items\": []` returns 201 with an empty batch. `\"items\"` with 10,000 entries holds the DB write lock for 10,000 carrier round-trips."
  },
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 24,
    "summary": "`_post` writes its own linear-backoff retry loop instead of reusing `parcelpost.retry.with_retry` (exponential backoff with jitter). The bulk handler and service also copy per-item validation and insert logic, and the service runs raw SQL instead of going through `shipments_repo`.",
    "failure_scenario": "There are now two retry policies with different backoff that drift apart. Bulk has already drifted from single create: missing event, different `declared_value` default. Calling `with_retry(..., retry_on=(URLError,))` in `_post`, and having bulk call a shared per-item helper plus a repo `get_many`, would remove the duplication."
  }
]
```