```json
[
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 25,
    "summary": "Retries now live in `_post`, so `create_label` gets retried as well. The label endpoint is not idempotent, and `services/shipments.py` has an explicit comment saying it must not be retried because a retry can buy a second label.",
    "failure_scenario": "With the default CARRIER_RETRIES=3, a POST /labels is sent and the carrier buys the label, but the connection then resets (URLError) or the carrier returns a 5xx (HTTPError, which subclasses URLError). `_post` sleeps and posts /labels again, and the carrier buys a second or third label. The customer is billed for every label but only one is recorded. This affects both single and bulk shipment creation."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 45,
    "summary": "The bulk batch is only all-or-nothing in the database. Labels bought at the carrier for earlier items are never voided when a later item fails and the transaction rolls back.",
    "failure_scenario": "items=[A, B, C] where get_rate fails for C. Labels for A and B are already bought, then ROLLBACK removes their rows. The response is 502 and nothing is saved, but two paid labels are left orphaned at the carrier with no record of them (`FakeCarrier.void_label` exists, yet nothing calls it). The test checks only that no rows were written, so it misses this."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 40,
    "summary": "Every carrier network call for the whole batch runs inside `BEGIN IMMEDIATE`, so SQLite's write lock is held for the length of N sequential HTTP calls plus their retries and backoff.",
    "failure_scenario": "A 20-item batch against a slow carrier (5s timeout, 3x3 retries plus sleeps) can hold the write lock for minutes. Concurrent POST /shipments requests and the inner `db.transaction()` of other requests hit 'database is locked' after sqlite's 5s busy timeout and fail with a 500. The single-shipment path deliberately makes its carrier calls before opening the transaction."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 47,
    "summary": "Bulk creation inserts shipment rows without calling `shipments_repo.add_event(shipment_id, 'labelled')`. This breaks the documented invariant that a shipment never exists without its 'labelled' event.",
    "failure_scenario": "POST /shipments/bulk with 2 items creates 2 shipments and 0 rows in shipment_events. Any event history, tracking or refund logic that expects the 'labelled' event will treat bulk shipments as malformed or skip them."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 42,
    "summary": "The call sites still wrap `get_rate` in `with_retry` (3 attempts, retrying on UpstreamError), and `_post` now retries 3 times internally before raising UpstreamError. The retries multiply to 9 HTTP attempts per rate call.",
    "failure_scenario": "Carrier is down, with timeout 5s: one quote or shipment makes 9 attempts, roughly 45s of timeouts plus linear and jittered sleeps, before returning 502. In bulk this happens per item while the DB write lock is held. The PR says retries 'now live in `_post`', but the `with_retry` wrappers in services/quotes.py and services/shipments.py were not removed."
  },
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 29,
    "summary": "`except urllib.error.URLError` also catches `HTTPError`, so permanent 4xx responses from the carrier are retried with backoff as if they were transient network errors.",
    "failure_scenario": "The carrier returns 400 for an unsupported destination. `_post` sleeps 0.5s and then 1.0s, retries the same bad request twice, and only then raises UpstreamError. Combined with the outer `with_retry` this is 9 requests and several seconds of added latency for a deterministic failure."
  },
  {
    "file": "parcelpost/handlers/shipments.py",
    "line": 35,
    "summary": "Bulk handles `declared_value` differently from POST /shipments, even though the PR says each item goes through the same validation. A missing value is stored as 0 cents instead of NULL, and an explicit null is rejected.",
    "failure_scenario": "An item without `declared_value` is saved with declared_cents=0 ('declared as worth zero') where single creation saves NULL ('not declared'). An item with `\"declared_value\": null` calls `to_cents(None)`, which raises 'declared_value is not a valid amount' (422), while the same payload is accepted by POST /shipments."
  },
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 34,
    "summary": "When `retries <= 0`, the loop never runs and `last` is still None, so `last.reason` raises AttributeError. Also, the constructor defaults to `retries=1` while config defaults to 3.",
    "failure_scenario": "Setting CARRIER_RETRIES=0 (for example to disable retries) means every carrier call raises AttributeError without sending a request. Each quote or shipment then returns a generic 500 'internal error' instead of making the call."
  },
  {
    "file": "parcelpost/handlers/shipments.py",
    "line": 28,
    "summary": "The handler never checks that `items` is a list of objects. Non-list or non-dict values crash with a 500 instead of returning a 422, and there is no limit on batch size.",
    "failure_scenario": "`{\"customer_id\":1,\"items\":\"abc\"}` iterates over characters and calls `require_fields('a', ...)`, which hits `'str'.get` and raises AttributeError (500). `\"items\": {\"x\": 1}` does the same over the dict's keys. `\"items\": [1]` gives `int.get`, also AttributeError. A batch of 10,000 items is accepted and holds the write lock for the entire run."
  },
  {
    "file": "parcelpost/clients/carrier.py",
    "line": 25,
    "summary": "`_post` hand-writes a second retry loop with linear backoff and no jitter, alongside `parcelpost/retry.py`'s `with_retry`, which already provides attempts, backoff, jitter and an injectable sleep.",
    "failure_scenario": "There are now two retry policies with different backoff (linear, no jitter versus exponential with full jitter) that stack on each other. With no jitter, many workers retry in lockstep during a carrier outage. `_post` could call `with_retry(..., retry_on=(urllib.error.URLError,))`, with retries removed from the call sites or kept off the label path."
  }
]
```