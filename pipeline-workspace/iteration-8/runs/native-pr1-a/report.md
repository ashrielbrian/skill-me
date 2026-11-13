```json
[
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 44,
    "summary": "The `status` query parameter goes straight into the SQL string through an f-string, which allows SQL injection. The handler also queries `db` directly, but the README says only repos call `parcelpost.db`.",
    "failure_scenario": "GET /customers/1/refunds?status=x' OR '1'='1 returns every customer's refunds. A payload like x' UNION SELECT ... can read any table. The value should be passed as a bound `?` parameter inside a repo function."
  },
  {
    "file": "parcelpost/repos/shipments.py",
    "line": 14,
    "summary": "Changing the cursor condition from `id > ?` to `id >= ?` makes each page start with the last item of the previous page. The test that walked every page and checked for no duplicates was rewritten into a weaker test that no longer catches this.",
    "failure_scenario": "Customer has shipments 1-5 and limit=2. Page 1 is [1,2] with next_cursor 2. Page 2 is [2,3]. Page 3 is [3,4], and so on. Every page repeats an item, and in some cases the client never reaches the end."
  },
  {
    "file": "parcelpost/services/refund_strategy.py",
    "line": 22,
    "summary": "The over-refund check compares only this one request with `charge_cents`. It ignores refunds already made and the shipment's 'refunded' status, so the total refunded can exceed the charge. The PR description says this cannot happen.",
    "failure_scenario": "Shipment charged 1999. POST /refund with key A and no amount refunds 1999. POST /refund again with key B (or with no header, which gets a random UUID) refunds another 1999. Total refunded is 3998 for a 1999 charge."
  },
  {
    "file": "parcelpost/utils.py",
    "line": 3,
    "summary": "`dollars_to_cents` converts with `int(float(amount) * 100)`, which truncates. It also duplicates `money.to_cents`, whose docstring warns about exactly this bug.",
    "failure_scenario": "amount \"19.99\" becomes 1998 cents, so the customer gets one cent less than requested. JSON floats are accepted when they should be rejected. A value like \"abc\" or \"nan\" raises ValueError, which becomes a 500 instead of a 422."
  },
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 22,
    "summary": "Idempotency is not actually enforced. A missing `Idempotency-Key` header is replaced by a random UUID. The `refunds.idempotency_key` column has no UNIQUE constraint. The lookup and the insert are not atomic. The key is not tied to a shipment.",
    "failure_scenario": "A client retries after a timeout without the header and gets two refunds. Two concurrent requests with the same key both see no existing row and both insert. Reusing a key for shipment 2 returns shipment 1's refund."
  },
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 31,
    "summary": "`except Exception` catches every error and returns 500 with `{\"error\": str(e)}`. This hides the router's AppError mapping and its protection against leaking internal error text.",
    "failure_scenario": "A refund on a missing shipment returns 500 with \"shipment 9 does not exist\" instead of 404. An over-limit amount returns 500 instead of 422. A sqlite or carrier error message, which can include SQL or upstream payloads, is sent to the client. The body also doesn't follow the documented `{code, message}` error shape. Using int() instead of parse_positive_int on the id also turns a bad id into a 500."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 52,
    "summary": "`_void_label` catches every exception, retries with a fixed `time.sleep(2)`, and returns normally after the third failure. It duplicates `retry.with_retry`, which the README calls the one retry policy for carrier calls.",
    "failure_scenario": "The carrier is down. The request thread blocks for about 6 seconds, then returns 201 with the shipment marked 'refunded' while the label is still live and billable. Nothing is logged or raised, so the failure is invisible."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 36,
    "summary": "The refund insert, the status update and the event insert are not wrapped in `db.transaction()`, unlike `create_shipment`. A crash between them leaves partial state.",
    "failure_scenario": "An exception after `refunds_repo.insert` (for example, a DB lock on `set_status`) leaves a refund row while the shipment is still 'labelled' and has no 'refunded' event. A retry with the same key then returns the existing refund, so the status is never fixed."
  },
  {
    "file": "parcelpost/services/refunds.py",
    "line": 39,
    "summary": "A partial refund still sets the shipment status to 'refunded' and voids the carrier label, as if it were a full refund.",
    "failure_scenario": "Refunding 5.00 of a 19.99 shipment as goodwill cancels the label and marks the shipment refunded, even though the customer paid for and still needs the shipment."
  },
  {
    "file": "parcelpost/handlers/refunds.py",
    "line": 11,
    "summary": "`_serialize` returns camelCase keys (`refundId`, `amountCents`, ...), but the README says all response keys are snake_case. In the same file, the service has narrating comments that break the CLAUDE.md comment rule.",
    "failure_scenario": "Clients that read `amount_cents`/`shipment_id`, as every other endpoint uses, get KeyErrors. ~/.claude/CLAUDE.md says \"Be frugal with comments. Only add comments when ... the code logic is complex\". services/refunds.py has lines like \"# Get the shipment\" and \"# Insert the refund\", plus a banner block. The single-implementation strategy ABC and registry, and the unused `list_for_shipment`/`SORT_COLUMNS`, are speculative extras."
  }
]
```