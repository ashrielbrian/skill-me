# PR Independent Review

## PR
acme/parcelpost#2 — Cap the listing page size at 100
https://github.com/acme/parcelpost/pull/2
3 files changed, +33/-2 lines, head 5be8f4a

## What this PR does (from the diff)
The PR adds the constant `MAX_LIMIT = 100` to `parcelpost/validation.py`. If the `limit` query value is more than 100, `parse_limit` now raises `ValidationError`, and the router returns a 422. The PR also adds `tests/test_validation.py`, which has unit tests for `parse_limit` and one endpoint test. It also adds "maximum 100" to the API table in the README.

## What the author says it does
The author says that `GET /customers/{id}/shipments` accepted any positive `limit`, so one request could get the full history of a customer. The PR caps `limit` at 100 and rejects a larger value with a 422, "the same way the other invalid values are". The default stays 20. The author says that the tests "cover the boundary (100 passes, 101 fails) and the endpoint response".

## The underlying problem
The listing endpoint has no maximum page size. One request can make the service read and return all the shipments of a customer. My minimal plan was this: add a maximum next to `DEFAULT_LIMIT`, check it in `parse_limit`, raise the existing `ValidationError`, update the README row, and add a test. The diff is the same size and shape as that plan, so it is proportionate.

## Summary
The change is correct and small, and it is in the correct layer. It uses the existing validation module, the existing error type, and the existing 422 path. The code agrees with the description. I found no Critical, High, or Medium issues. The most important finding is Low: the tests get the boundary from `MAX_LIMIT`, so no test fails if the documented value of 100 changes.

## Findings

### [Low] The tests do not lock the documented maximum of 100
- **Category**: Testing
- **Location**: tests/test_validation.py:12, tests/test_validation.py:17, tests/test_validation.py:24 (head 5be8f4a)
- **Problem**: Each boundary test gets its input from the `MAX_LIMIT` constant. If a person changes `MAX_LIMIT` to 1000, all the tests still pass. README.md:24 would then show a wrong maximum, and no test would fail. The PR description says "100 passes, 101 fails", but no test uses the literal values 100 or 101. The endpoint test checks only the case where the request fails. No test shows that the endpoint returns 200 for `limit=100`.
- **Evidence**:
  - `@pytest.mark.parametrize("value", ["1", "20", str(MAX_LIMIT)])` (tests/test_validation.py:12)
  - `@pytest.mark.parametrize("value", [str(MAX_LIMIT + 1), "0", "-5", "many"])` (tests/test_validation.py:17)
  - `query = {"limit": str(MAX_LIMIT + 1)}` (tests/test_validation.py:24)
  - The sibling parser tests use literal values for the contract: `[("19.99", 1999), ("0.01", 1), ("5", 500), ...]` (tests/test_money.py:9).
- **Suggested fix**: Use the literal strings `"100"` and `"101"` in the parametrize lists. Add one endpoint assertion that `limit=100` returns status 200.
- **Effort**: Small (< 1 hour)

### [Low] The endpoint test is in the unit-test file for `validation.py`
- **Category**: Testing
- **Location**: tests/test_validation.py:23 (head 5be8f4a)
- **Problem**: `test_listing_over_the_maximum_is_422` sends a request to the shipments route, but it is in the unit-test file for `validation.py`. The repo keeps HTTP tests for a route in the test file of that resource. A person who looks for the listing endpoint tests in `tests/test_shipments.py` will not find this test. The new file also has to import `parcelpost.http` only for this one test.
- **Evidence**:
  - HTTP validation tests for shipments: `test_create_shipment_validates` (tests/test_shipments.py:20), `test_get_missing_shipment_is_404` (tests/test_shipments.py:26).
  - HTTP test for quotes: `test_quote` (tests/test_quotes.py:8).
  - Unit tests for a parser module, without `dispatch`: tests/test_money.py:11, tests/test_money.py:16.
- **Suggested fix**: Move `test_listing_over_the_maximum_is_422` to `tests/test_shipments.py`. Remove the `Request, dispatch` import from `tests/test_validation.py`.
- **Effort**: Small (< 1 hour)

### [Low] Pre-existing: on the same endpoint, a bad `after` value gives a 500, not a 422
- **Category**: Error handling
- **Location**: parcelpost/handlers/shipments.py:31 (head 5be8f4a). This line is not in the diff.
- **Problem**: The handler converts `after` with a bare `int()`. A value that is not an integer, such as `?after=abc`, raises `ValueError`. The router at parcelpost/http.py:54-58 then returns a 500 "internal error" and writes a stack trace with `log.exception`. The PR makes `limit` fail "the same way the other invalid values are", but the other query value on this endpoint does not follow that rule. A client error then shows as a server error in the logs and in the 5xx counts.
- **Evidence**:
  - `after_id = int(request.query.get("after", 0))` (parcelpost/handlers/shipments.py:31).
  - I ran the handler at head 5be8f4a with `{'after': 'abc'}`. It returned `500 {'error': {'code': 'internal', 'message': 'internal error'}}` and logged `ValueError: invalid literal for int() with base 10: 'abc'`.
  - The other inputs on the same handler use the validators: `parse_positive_int` (parcelpost/handlers/shipments.py:30) and `parse_limit` (parcelpost/handlers/shipments.py:32).
- **Suggested fix**: Keep this out of this PR and fix it in a follow-up PR. Add a `parse_cursor` function to `parcelpost/validation.py`. Make it return 0 when `after` is missing. Make it raise `ValidationError` when the value is not an integer or is less than 0.
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Architectural fit**: The cap is in the correct layer. README.md:9 says that handlers validate input. `MAX_LIMIT` is next to `DEFAULT_LIMIT` (parcelpost/validation.py:3-4 at head). The new check raises the same `ValidationError` as `parse_positive_int` (parcelpost/validation.py:17-19 at head). The PR does not duplicate an existing helper.
- **Simplicity & economy**: The PR adds no new layer, config key, wrapper, or comment. The new code is one constant and one check of three lines.
- **API & interface design**: The error has the standard shape, and its code is `invalid_request`. The message "limit must be at most 100" follows the style of "limit must be positive". The README row is updated. See Open Questions for the effect on current clients.
- **Correctness**: I checked `parse_limit` and the endpoint at head 5be8f4a with these inputs:
  - Accepted: `"1"`, `"20"`, `"100"`, `" 100 "`.
  - Rejected with a 422: `"101"`, `"0"`, `"-5"`, `"many"`, `"100.0"`, `None`.
  - The `next_cursor` logic at parcelpost/services/shipments.py:42 still works, because the service gets the same `limit` that the handler validated.
- **Intent vs implementation**: There is no harmful mismatch. The PR also has one good effect that the description does not mention. On main, `limit=99999999999999999999` went to the SQL `LIMIT ?` bind and the request returned a 500. At head, the request returns a 422 with "limit must be at most 100".
- **Security**: The PR adds no new attack surface. One request can now read 100 rows or fewer. The PR adds no new log lines.
- **Performance**: The query uses the `shipments_customer` index on `(customer_id, id)` (parcelpost/schema.sql:14). The cap limits each page to 100 rows.
- **Observability**: Validation errors do not write logs today, and the PR does not change that.
- **Scope & PR hygiene**: The PR does one thing and has no unrelated edits. It is easy to revert, because it changes no schema and no stored data.
- **Test run**: pytest is not installed on this machine, so I did not run `python -m pytest`. To check the behavior above, I ran a script against head 5be8f4a in a temporary worktree. I removed the worktree and the local branch after the check.

## Open Questions
1. **Do current clients send `limit` values above 100?** On main, `limit=101` returns 200 (I checked). After this PR, the same request returns 422, so this is a breaking change for any client that sends a larger value. A 422 agrees with all the other validators in the repo. To clamp the value to 100 would keep old clients working, but `next_cursor` would then be `None` on a full page of 100 rows when the client asked for 101 or more. Access logs that show the `limit` values on this endpoint, and a list of its clients, would answer this question.
2. **Is 100 the correct maximum?** The code cannot show whether a client needs more than 100 rows on one page, for example an export job. The product owner or the clients of the endpoint can answer this question.
3. **Must the API change appear in a version or a changelog?** pyproject.toml:3 has version `0.4.0`, and the PR does not change it. The repo has no changelog file. The release process of the team will answer this question.