# PR Independent Review

## PR
acme/parcelpost#2 — Cap the listing page size at 100
https://github.com/acme/parcelpost/pull/2
3 files changed, +33/-2 lines, head 5be8f4a

## What this PR does (from the diff)
The PR adds a `MAX_LIMIT = 100` constant in `parcelpost/validation.py`. `parse_limit` now raises `ValidationError` when the parsed `limit` is more than 100. The PR adds `tests/test_validation.py`, which has unit tests for `parse_limit` and one endpoint test. The PR also adds "maximum 100" to the README API table.

## What the author says it does
The author says that `GET /customers/{id}/shipments` accepted any positive `limit`, so one request could get the full history of a customer. The PR caps `limit` at 100 and returns 422 for a larger value. The default stays at 20. The tests cover the boundary (100 passes, 101 fails) and the endpoint response.

## The underlying problem
A client can ask for a page of any size from `GET /customers/{id}/shipments`, so one request can read all of a customer's shipments in one SQL query. The diff is proportionate. My minimal sketch was a bound in `parse_limit`, a change to `README.md:24`, and a test, and the diff is the same as the sketch.

## Summary
The change is small and correct, and it is in the right place. The new check is in the shared helper for validation, and it uses the current `ValidationError` → 422 path in `parcelpost/http.py:52-53`. I examined each claim in the description and found that each one is true. I found no significant problem. One compatibility question remains for the author (see Open Questions).

Results of my checks at 5be8f4a (in a temporary worktree, which I removed after the checks):
- All 28 tests pass (`uv run --with pytest python -m pytest`).
- I ran the new tests against the base body of `parse_limit`. Two tests fail: `test_parse_limit_rejects[101]` and `test_listing_over_the_maximum_is_422`. Thus the new tests are real regression tests.
- A direct request with `limit=100` returns 200. A request with `limit=101` returns 422 with `{"code": "invalid_request", "message": "limit must be at most 100"}`.

## Findings
No findings.

## Issues Not Found
Architectural fit, Simplicity & economy, API & interface design, Best practices & idioms, Correctness, Security, Error handling, Performance, Testing, Observability, Maintainability, Scope & PR hygiene, Intent vs implementation.

- **Architectural fit**: The check is in `parcelpost/validation.py` next to `parse_positive_int` (`parcelpost/validation.py:12`) and `parse_country` (`parcelpost/validation.py:28`). This agrees with the rule in `README.md:9` that "A handler validates input". The only caller of `parse_limit` is `parcelpost/handlers/shipments.py:32`.
- **Correctness**: The pagination logic in `parcelpost/services/shipments.py:42` does not change. The service gets a `limit` of 100 or less, and `next_cursor` is still set when a page is full.
- **Testing**: The new test file uses the same pattern as `tests/test_money.py:7-18`, which has parametrized "accepts" and "rejects" tests. The endpoint test also checks the error `code`, not only the status.

## Open Questions
1. **Do current clients send `limit` above 100?** Before this PR, `?limit=500` returned 200 with a maximum of 500 items. After this PR, the same request returns 422. The description shows that the author chose 422 on purpose, and 422 agrees with how the API treats other values that are not valid. If some clients send values above 100, this change will break them. A clamp to 100 keeps those clients working, because `next_cursor` is already set when the page is full (`parcelpost/services/shipments.py:42`). To answer this question, get the `limit` values in the access logs, or get a list of the API clients.