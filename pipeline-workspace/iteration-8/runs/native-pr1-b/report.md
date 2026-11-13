I reviewed PR #1 ("Add shipment refunds", `refunds` → `main`). I didn't run a verification pass, as the recipe specifies. The most serious problems: an SQL injection in the refund listing, no limit on the total refunded per shipment, and a regression in shipment pagination. Some lower-priority items were cut to stay within the 10-finding cap: an unused `list_for_shipment`/`SORT_COLUMNS`, a `RefundStrategy` abstraction with only one implementation, and a test assertion that checks nothing (`assert MagicMock() is not None`).

```json
[
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 44,
    "summary": "The `status` query parameter is inserted into the SQL with an f-string instead of a bound parameter, which allows SQL injection.",
    "failure_scenario": "GET /customers/1/refunds?status=x' OR '1'='1 removes the customer filter and returns every customer's refunds; a UNION SELECT payload can read any table."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 33,
    "summary": "The refund amount is checked against `charge_cents` only, ignoring what was already refunded and whether the shipment is already 'refunded', so the total refunded can exceed the charge.",
    "failure_scenario": "On a 1999-cent shipment, two POST /shipments/1/refund calls with different Idempotency-Keys (or none) each pass validation, refunding 3998 cents and voiding the label twice. This contradicts the PR's claim that a shipment 'can never be refunded for more than it was charged'."
  },
  {
    "file": "parcelpost/repos/shipments.py",
    "line": 14,
    "summary": "Changing `id > ?` to `id >= ?` breaks cursor pagination: `next_cursor` is the last id on the current page, so every following page repeats that row. The test that walked every shipment exactly once was replaced with a weaker one.",
    "failure_scenario": "Shipments 1..5 with limit=2: pages are [1,2], [2,3], [3,4], [4,5], [5] (duplicates). With limit=1, after=1 returns [1] and next_cursor=1 forever, so clients loop endlessly."
  },
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 22,
    "summary": "Idempotency is not guaranteed. A missing Idempotency-Key gets a fresh random UUID, the check-then-insert has no transaction and no UNIQUE constraint on `refunds.idempotency_key`, and the key is not scoped to the shipment.",
    "failure_scenario": "A client retrying without the header gets a second refund. Two concurrent requests with the same key both miss `get_by_idempotency_key` and both insert. Reusing a key for shipment 2 silently returns shipment 1's refund."
  },
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 30,
    "summary": "`except Exception` turns every error into a 500 with `str(e)`. This hides ValidationError/NotFound status codes and bypasses dispatch()'s sanitization of internal messages.",
    "failure_scenario": "An amount above the charge returns 500 instead of 422, and a missing shipment returns 500 instead of 404. A non-numeric id or a sqlite/carrier error sends raw exception text (SQL, upstream payloads) to the client, and the error body shape `{error: str}` differs from the app's `{error: {code, message}}`."
  },
  {
    "file": "parcelpost/utils.py",
    "line": 3,
    "summary": "`dollars_to_cents` converts through float and truncates, re-implementing `money.to_cents` badly; that function exists specifically to reject floats and round correctly.",
    "failure_scenario": "amount='19.99' becomes int(19.99*100) = 1998 cents, so the customer is under-refunded by 1 cent. Float/bool JSON input is accepted, and 'abc' or 'nan' raise ValueError, which becomes a 500 instead of a 422. Use `money.to_cents(amount, 'amount')` and delete utils.py."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 56,
    "summary": "`_void_label` catches every Exception, sleeps a fixed 2s, and returns normally after the third failure, so voiding the label is silently skipped. It also re-implements `retry.with_retry`.",
    "failure_scenario": "Carrier is down: the request blocks about 6s on the request thread, then returns 201 with the shipment marked 'refunded' while the label stays live at the carrier. Nothing is logged or raised. `with_retry(lambda: client.void_label(...))` gives jittered backoff and propagates the final failure."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 36,
    "summary": "The refund insert, status update and 'refunded' event are separate autocommit statements with no `db.transaction()`, unlike `create_shipment`, which commits its row and event together.",
    "failure_scenario": "If set_status or add_event fails after the refund row is inserted, a refund exists but the shipment is not 'refunded' and has no event. An Idempotency-Key retry then returns the existing refund and never repairs the state."
  },
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 11,
    "summary": "`_serialize` uses camelCase keys (refundId, shipmentId, amountCents, createdAt), breaking the documented API contract.",
    "failure_scenario": "The README says 'All response keys are snake_case. All money fields are integer cents and end in `_cents`'. Clients reading `amount_cents`/`refund_id` like every other endpoint get KeyErrors or undefined values."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 13,
    "summary": "Banner comments and line-by-line narration comments violate ~/.claude/CLAUDE.md: 'Be frugal with comments. Only add comments when: the code logic is complex, non-trivial or non-obvious'.",
    "failure_scenario": "Comments such as '# Get the shipment' above `shipments_repo.get(shipment_id)`, '# Insert the refund' and '# Update the shipment status', plus the '# ===== Refund service =====' banner, only restate obvious code. Meanwhile the one non-obvious decision, voiding the label after the DB commit, has no comment."
  }
]
```