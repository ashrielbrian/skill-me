# PR Independent Review

Review of **acme/parcelpost#2: "Cap the listing page size at 100"**. I did not read the review comments or threads on the PR.

## PR
acme/parcelpost#2: Cap the listing page size at 100
https://github.com/acme/parcelpost/pull/2
3 files changed, +33/-2 lines, head `5be8f4a`

## What this PR does (from the diff)
The PR adds the constant `MAX_LIMIT = 100` to `parcelpost/validation.py`. When the parsed `limit` is more than 100, `parse_limit` now raises `ValidationError`. The PR also adds `tests/test_validation.py`, which has unit tests for `parse_limit` and one endpoint test. It also adds "maximum 100" to the API table in the README.

## What the author says it does
The author says that `GET /customers/{id}/shipments` accepted any positive `limit`, so one request could get the full history of a customer. The PR caps `limit` at 100 and returns a 422 for a larger value, the same as for other invalid values. The default stays 20. The tests cover "100 passes, 101 fails" and the endpoint response.

## The underlying problem
Before this PR, one request to the listing endpoint could ask for any number of shipment rows. The SQL `LIMIT` and the response size grew with the value from the client.

My minimal solution has four parts: a constant, a check in `parse_limit`, a README line, and a boundary test. The diff has the same shape, so it is proportionate.

## Summary
The change is small and correct, and it is in the correct layer. Each claim in the description matches the diff.

pytest is not installed in this environment. I ran a script against the PR head with the same inputs as the new tests:
- `"100"` returns 100.
- `"101"` gives `ValidationError("limit must be at most 100")`.
- `"0"`, `"-5"`, and `"many"` fail as before.
- The endpoint returns 422 with code `invalid_request` for `limit=101`, and 200 for `limit=100`.

I found no significant issues. One open question remains: do any clients send a `limit` above 100 today?

## Findings

None.

## Issues Not Found
No findings in any category: Architectural fit, Simplicity & economy, API & interface design, Best practices & idioms, Correctness, Security, Error handling, Performance, Testing, Observability, Maintainability, Scope & PR hygiene, Intent vs implementation.

- **Architectural fit:** The cap is next to `DEFAULT_LIMIT` in `parcelpost/validation.py:3-4`. The README gives input validation to the handler layer (`README.md:9`). `parse_limit` has one caller, `parcelpost/handlers/shipments.py:32`, so the cap covers the only entry point.
- **API & interface design:** The new error uses the existing `ValidationError` contract: status 422, code `invalid_request` (`parcelpost/errors.py:13-15`). The message format "limit must be at most 100" matches the existing messages at `parcelpost/validation.py:17` and `parcelpost/validation.py:19`.
- **Testing:** The tests check behavior at both sides of the boundary (`MAX_LIMIT` and `MAX_LIMIT + 1`). The endpoint test checks the HTTP status and the error code, not only that an exception occurs.

## Open Questions
- **Does any current client send `limit` above 100?** Before this PR, such a request returned 200. After this PR, it returns 422. The PR description does not mention this compatibility effect. The access logs for `GET /customers/{id}/shipments` with `limit` > 100 will answer this question.
  - If such clients exist, the alternative is to clamp the value to 100 in `parse_limit`. `list_shipments` computes `next_cursor` from the limit that it gets (`parcelpost/services/shipments.py:42`). Thus a client that follows `next_cursor` still gets every row.
  - A clamp has a risk too: a client that ignores `next_cursor` silently gets fewer rows. The PR rejects the value instead. That choice is the stricter contract, and it is easier to relax later.