I reviewed PR #2 ("Cap the listing page size at 100", branch `limit-cap`). The diff is small: `MAX_LIMIT` and its check in `parse_limit`, a new `tests/test_validation.py`, and one README line. The cap itself is correct: 100 passes, 101 is rejected with a 422, and the default stays 20. I found no new correctness bug in the changed lines. The most severe finding is an older bug in the same handler. I reproduced it on the branch: `after=abc` returns a 500. I couldn't run the test suite because pytest isn't installed for the available `python3`.

```json
[
  {
    "file": "parcelpost/handlers/shipments.py",
    "line": 31,
    "summary": "The `after` cursor on the listing endpoint the PR hardens is parsed with a bare `int()`, so a non-integer cursor raises ValueError and the client gets a 500 instead of the 422 that every other invalid query value gets.",
    "failure_scenario": "GET /customers/1/shipments?after=abc -> ValueError: invalid literal for int() -> dispatch's catch-all returns 500 {\"code\":\"internal\"} (reproduced on the limit-cap branch). The PR's own description says invalid values are rejected with 422, but this one is not; `after` should go through a validator such as parse_positive_int or a non-negative variant."
  },
  {
    "file": "parcelpost/validation.py",
    "line": 28,
    "summary": "A `limit` above 100 is now rejected with a 422 instead of being reduced to 100, which breaks existing clients that send a larger page size and worked before this change.",
    "failure_scenario": "A client that pages with ?limit=500 used to get 500 rows and a cursor. After deploy every request fails with 422 'limit must be at most 100', so its sync stops completely. Reducing the value to MAX_LIMIT (min(limit, MAX_LIMIT)) would cap the load without the breaking change, since the cursor already lets clients fetch the rest."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 41,
    "summary": "The cap is enforced only in the HTTP query parser. `list_shipments` and `shipments_repo.list_for_customer` still accept any limit, so any caller that skips `parse_limit` can still read a customer's whole history in one call.",
    "failure_scenario": "A future handler, job, or internal caller calls shipments_service.list_shipments(customer_id, 10_000) and gets an unbounded query, the same problem the PR set out to fix. Enforcing or reducing to MAX_LIMIT in the service (or repo) covers every caller, not just one request path."
  },
  {
    "file": "parcelpost/services/shipments.py",
    "line": 43,
    "summary": "`next_cursor` is set whenever a page comes back exactly full, so a customer whose shipment count is an exact multiple of `limit` always gets one extra, empty page.",
    "failure_scenario": "A customer with exactly 100 shipments and limit=100: page 1 returns 100 items and next_cursor=<last id>; page 2 returns [] with next_cursor=None. Every such client makes an extra round trip and DB query. Fetching limit+1 rows and trimming the last one would detect the end correctly."
  },
  {
    "file": "tests/test_validation.py",
    "line": 24,
    "summary": "The endpoint-level test for the listing route is in the new test_validation.py, while every other endpoint test for this route (including the pagination test) is in tests/test_shipments.py.",
    "failure_scenario": "Listing-endpoint behaviour is now tested in two files. Someone changing the /customers/{id}/shipments contract finds the pagination test in test_shipments.py and misses the 422 test in test_validation.py. Moving test_listing_over_the_maximum_is_422 into test_shipments.py keeps the endpoint tests in one place."
  },
  {
    "file": "tests/test_validation.py",
    "line": 24,
    "summary": "No test checks that a valid limit at the boundary (limit=100) works through the HTTP endpoint and paginates correctly. Only the 101 rejection is tested end to end.",
    "failure_scenario": "A later change that makes the handler reject or mishandle limit=100 (for example, an off-by-one moved into the handler, or an inconsistent cap in the service) would pass the suite, because only parse_limit is tested at 100."
  }
]
```