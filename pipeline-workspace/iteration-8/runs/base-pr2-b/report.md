# PR Independent Review

## PR
acme/parcelpost#2 — Cap the listing page size at 100
https://github.com/acme/parcelpost/pull/2
3 files changed, +33/-2 lines, head 5be8f4a

## What this PR does (from the diff)
The PR adds the constant `MAX_LIMIT = 100` to `parcelpost/validation.py`. `parse_limit` now raises `ValidationError` when `limit` is more than 100.

The PR also adds the file `tests/test_validation.py`. It has unit tests for `parse_limit` and one test of the endpoint. The PR adds "maximum 100" to the API table in the README.

## What the author says it does
The author says that `GET /customers/{id}/shipments` accepted any positive `limit`, so one request could get the full history of a customer. The PR caps `limit` at 100 in `parse_limit`. A larger value gets a 422, "the same way the other invalid values are". The default stays 20. The author says that the tests cover the boundary ("100 passes, 101 fails") and the endpoint response.

## The underlying problem
A client can ask `GET /customers/{id}/shipments` for a page of any size. One request can then read all of a customer's shipments from the database and send them back in one response.

I made a minimal sketch from the precedents in the repo. It has four parts:
- a maximum constant next to `DEFAULT_LIMIT`
- one bound check in `parse_limit`
- one README change
- a boundary test

The diff is the same as this sketch, so its size is proportionate.

## Summary
The change is small and correct, and it is in the correct layer. The diff does what the description says. `parse_limit` is the only parser for `limit`, and `parcelpost/handlers/shipments.py:32` is its only HTTP caller. Thus the cap covers all HTTP access to the listing.

I found one Low finding, in the documentation. The most important item is not a code defect. A client that sends `limit` above 100 now gets an error (see Open Questions). At head 5be8f4a, all 28 tests pass. I ran them in a temporary worktree and then removed the worktree.

## Findings

### [Low] The README does not say what happens above the maximum
- **Category**: API & interface design
- **Location**: README.md:24 (head 5be8f4a)
- **Problem**: The README now says that `limit` has a maximum of 100. It does not say that a larger value gets a 422. Many APIs with pages clamp a large page size to the maximum and return 200. A client author who reads "maximum 100" can expect a clamp. That client then gets an error that it does not handle.
- **Evidence**: README.md:24 says: "Query: `limit` (default 20, maximum 100), `after` (cursor)." The code at `parcelpost/validation.py:27-28` rejects the value: `if limit > MAX_LIMIT: raise ValidationError(f"limit must be at most {MAX_LIMIT}")`. At head, `limit=101` returns `422 {'error': {'code': 'invalid_request', 'message': 'limit must be at most 100'}}`.
- **Suggested fix**: Add the reject behavior to the README row. Example: "`limit` (default 20, maximum 100; a larger value returns 422 `invalid_request`)".
- **Effort**: Small (< 1 hour)

## Issues Not Found
- **Architectural fit**: No finding.
  - `MAX_LIMIT` is next to `DEFAULT_LIMIT` (`parcelpost/validation.py:3-4`).
  - The bound check is in the shared parser that the handler already calls. This agrees with README.md:9: "A handler validates input".
  - The sibling validators also raise `ValidationError` for a bad value and do not clamp it: `parse_positive_int` (`parcelpost/validation.py:13-20`), `parse_country` (`parcelpost/validation.py:29-32`), and `to_cents` (`parcelpost/money.py:12-19`).
  - The service function `list_shipments` (`parcelpost/services/shipments.py:40`) has no cap. But only the handler calls it from HTTP, so this is acceptable.
- **Simplicity & economy**: No finding. The diff adds one constant and three lines of logic. It adds no new layer, config key, or wrapper.
- **Correctness**: No finding.
  - The check is `limit > MAX_LIMIT`, so 100 passes and 101 fails. This agrees with the README and the tests.
  - The check runs after `parse_positive_int`. Thus zero, negative, and non-integer values get the same errors as before.
  - The `next_cursor` logic at `parcelpost/services/shipments.py:42` does not change.
- **Security**: No finding. The PR adds no new attack surface. It decreases the maximum cost of one request.
- **Error handling**: No finding in the diff. The same endpoint has a defect that was there before this PR, and the PR does not touch it:
  - `parcelpost/handlers/shipments.py:31` calls `int(request.query.get("after", 0))` and does not validate the value.
  - At head, `after=abc` returns 500 `internal`, and `parcelpost/http.py:57` logs a stack trace.
  - Fix this in a different PR. Use a parser that raises `ValidationError`, the same as `parse_positive_int` does.
- **Performance**: No finding. One request now reads 100 rows or fewer from `SELECT *` (`parcelpost/repos/shipments.py:14`).
- **Testing**: No finding.
  - The tests check the values 1, 20, 100, and 101. They also check the old invalid values "0", "-5", and "many".
  - `test_listing_over_the_maximum_is_422` (`tests/test_validation.py:23`) sends the request through `dispatch` and checks the status and the error code.
  - If you remove the check, the tests for 101 fail.
  - The tests for the other endpoints are in `tests/test_shipments.py:20` and `tests/test_shipments.py:26`. But `tests/test_quotes.py:15` also tests a module other than quotes, so the repo has no strict rule about where tests go.
- **Observability**: No finding. The router does not log a 422 from an `AppError`, the same as for the other validation errors (`parcelpost/http.py:52-53`).
- **Maintainability / Scope & PR hygiene**: No finding. The PR does one thing. It does not change the schema or the data, so you can revert it with no other changes.
- **Intent vs implementation**: The description is correct. The diff also fixes one thing that the description does not mention:
  - On `main` (c386fca), `limit=99999999999999999999` returns 500. The SQLite bind raises `OverflowError: Python int too large to convert to SQLite INTEGER`.
  - At head, the same request returns 422 "limit must be at most 100".
  - This change that the description leaves out does no harm.

## Open Questions
1. **Do current clients send `limit` above 100?**
   - On `main`, `limit=500` returns 200 with up to 500 items. At head, the same request returns 422.
   - Some clients may use one large page to get the full history and not follow `next_cursor`. These clients now get an error.
   - The access logs for the `limit` query value on `GET /customers/{id}/shipments` can answer this question.
   - The repo has no CHANGELOG file, and the version is 0.4.0 (`pyproject.toml:3`). If such clients exist, the author must decide on one of two options. Option 1 is a release note. Option 2 is a short period that clamps the value, before the change to a 422.