# PR Independent Review

## PR
acme/parcelpost#2 — Cap the listing page size at 100
https://github.com/acme/parcelpost/pull/2
3 files changed, +33/-2 lines, head 5be8f4a

## What this PR does (from the diff)
The PR adds the constant `MAX_LIMIT = 100` to `parcelpost/validation.py`. Now `parse_limit` raises `ValidationError` when the parsed `limit` is more than 100. The PR also adds `tests/test_validation.py`, with unit tests for `parse_limit` and one endpoint test. It also adds "maximum 100" to the README row for `GET /customers/{id}/shipments`.

## What the author says it does
The author says that `GET /customers/{id}/shipments` accepted any positive `limit`, so one request could get the full history of a customer. The PR caps `limit` at 100 in `parse_limit` and returns a 422 for a larger value. The default stays 20. The author also says that the tests cover the boundary ("100 passes, 101 fails") and the endpoint response, and that the README is updated.

## The underlying problem
A client can ask for an unlimited number of shipment rows in one request to the listing endpoint. One request can then load the full history of a customer into memory and into the response.

My minimal sketch has four parts: a maximum constant next to `DEFAULT_LIMIT`, one comparison in `parse_limit`, the README row, and boundary tests. The diff has the same footprint, so the change is proportionate.

## Summary
The change is small and correct, and it is in the correct layer. Each claim in the description matches the diff. I checked the behavior at head 5be8f4a with a script, because no Python interpreter on this machine has pytest. I found no significant issue in the PR. The most important item is Open Question 1: current clients that send `limit` above 100 will get a 422 after deploy. I also report one Low defect in the same handler. The PR did not cause it.

## Findings

### [Low] Older defect in the same handler: a non-integer `after` gives a 500, not a 422
- **Category**: Correctness
- **Location**: parcelpost/handlers/shipments.py:31 (head 5be8f4a)
- **Problem**: This PR did not add this defect and does not change this line. The handler converts `after` with a bare `int()`. A value such as `after=abc` raises `ValueError`. `dispatch` treats it as an unexpected exception. It logs a traceback at error level and returns 500 `internal`. Thus a client input error shows as a server error in logs and alerts. The PR description says that invalid values get a 422 "the same way the other invalid values are". That is true for `limit`, but it is not true for `after` on the same endpoint.
- **Evidence**:
  ```python
  after_id = int(request.query.get("after", 0))
  ```
  At head 5be8f4a, `dispatch(Request("GET", "/customers/1/shipments", query={"after": "abc"}))` returned `500 {'error': {'code': 'internal', 'message': 'internal error'}}`. It also logged `unhandled error in GET /customers/1/shipments`. The other inputs on this endpoint use validators:
  - `parse_positive_int(request.params["id"], "id")` at parcelpost/handlers/shipments.py:30.
  - `parse_limit(request.query)` at parcelpost/handlers/shipments.py:32.

  The generic 500 path is at parcelpost/http.py:54-58.
- **Suggested fix**: Do not block this PR. Fix it in a separate PR. Add a validator that accepts 0 and positive integers and raises `ValidationError` for other values. You cannot use `parse_positive_int` (parcelpost/validation.py:13) as it is, because it rejects 0, and 0 is the default cursor. Add a test that expects a 422 for `after=abc`.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Architectural fit**: The cap is in `parse_limit`, next to `DEFAULT_LIMIT` (parcelpost/validation.py:3-4). This follows the README rule that "A handler validates input" (README.md:9). The PR uses the current `ValidationError` (parcelpost/errors.py:13) and adds no new error type. The message "limit must be at most 100" has the same form as `f"{field} must be positive"` (parcelpost/validation.py:19).
- **Simplicity & economy**: The PR adds no new layer, parameter, wrapper, or comment. The diff is the minimal shape.
- **API & interface design**: The 422 uses the one error shape that README.md:28 documents. The README row now shows the maximum. The change is not backward compatible for clients that send more than 100. See Open Question 1.
- **Correctness**: The boundary is correct. The values `1`, `100`, and `" 100 "` pass. The values `101`, `0`, `-5`, `many`, `""`, `1e2`, and `100.0` give a 422. The service compares `len(items) == limit` with the same `limit` (parcelpost/services/shipments.py:42), so `next_cursor` still works.
- **Security**: The PR removes an unbounded read. It adds no new input path and no dependency. The error message does not echo user input.
- **Error handling**: The new path raises `ValidationError`, and `dispatch` maps it to a 422 (parcelpost/http.py:52-53).
- **Performance**: Each page now has at most 100 rows. The index `shipments_customer ON shipments (customer_id, id)` (parcelpost/schema.sql:14) covers the query at parcelpost/repos/shipments.py:14.
- **Testing**: The tests cover the default, the accepted values 1, 20, and 100, the rejected values 101, 0, -5, and "many", and the endpoint 422.
  - The `101` unit case and `test_listing_over_the_maximum_is_422` (tests/test_validation.py:23-27) fail without the fix, so they are regression tests.
  - I could not run pytest, because no interpreter here has it installed. A script at the head SHA showed the same results that the tests assert.
  - The endpoint test is in `tests/test_validation.py`. Most endpoint tests are in `tests/test_shipments.py` (lines 20 and 26). But `tests/test_quotes.py` mixes unit and endpoint tests, so the repo does not have a strict rule. This is not a finding.
- **Observability**: The codebase does not log 4xx validation errors. The PR keeps that convention.
- **Maintainability**: No issue.
- **Scope & PR hygiene**: The PR does one thing and has no unrelated edits. A revert is clean, because the PR has no migration and no data change.
- **Intent vs implementation**: Each claim in the description matches the diff: the cap, the 422, the default of 20, the boundary tests, the endpoint test, and the README.

## Open Questions
1. **Do current clients send `limit` above 100?** Before this PR, these requests returned all the rows. After this PR, they get a 422. The PR rejects a large value. It does not reduce the value to 100. Rejection agrees with the codebase precedent: `parse_positive_int` rejects 0 and does not change it to 1 (parcelpost/validation.py:18-19). Request logs that show `limit` values above 100 will answer this question. If such clients exist, tell them before deploy. As an alternative, reduce large values to 100 for one release, and then reject them.
2. **Must every customer listing endpoint have this cap?** The open branch `origin/refunds` (cc3d8f2) adds `GET /customers/{id}/refunds` in `parcelpost/handlers/refunds.py`. That endpoint has no `limit` and returns all rows. If the goal is that no single request returns the full history of a customer, that endpoint also needs `parse_limit` and `MAX_LIMIT`. This is outside the scope of this PR. The merge order and the intent of the refunds author will answer this question.