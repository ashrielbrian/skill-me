# PR Independent Review

## PR
acme/parcelpost#2 — Cap the listing page size at 100
https://github.com/acme/parcelpost/pull/2
3 files changed, +33/-2 lines, head 5be8f4a

## What this PR does (from the diff)
The PR adds a `MAX_LIMIT = 100` constant to `parcelpost/validation.py`. `parse_limit` now raises `ValidationError` when the parsed `limit` is more than 100. The PR also adds `tests/test_validation.py` with unit tests for `parse_limit` and one endpoint test, and it adds "maximum 100" to the README row for `GET /customers/{id}/shipments`.

## What the author says it does
The author says that `GET /customers/{id}/shipments` accepted any positive `limit`, so one request could get the whole history of a customer. The PR caps `limit` at 100 and returns a 422 for a larger value, "the same way the other invalid values are". The default stays 20.

## The underlying problem
A client can ask for an unlimited number of rows in one listing request, so the cost of one request has no limit. The diff matches the smallest possible fix: one constant, one check in the parse function that already exists, one README row, and tests. The PR adds no extra layers.

## Summary
The change is small and correct, and it puts the check in the right place. I found no significant issues. All the author's claims are true when I check them against the code. The only open item is a product decision: should a value above 100 get a 422 (a breaking change for existing clients) or be silently capped?

## Findings

No findings.

## Issues Not Found
I found no issues in any category: Architectural fit, Simplicity & economy, API & interface design, Best practices & idioms, Correctness, Security, Error handling, Performance, Testing, Observability, Maintainability, Scope & PR hygiene, Intent vs implementation.

- **Architectural fit**: The check is in `parse_limit` (`parcelpost/validation.py:23-29` at 5be8f4a). This matches the README rule that "A handler validates input" (`README.md:9`). `parse_limit` has one caller (`parcelpost/handlers/shipments.py:32`). The new check uses the same `ValidationError` and the same message style as `parse_positive_int` (`parcelpost/validation.py:16-18` on `main`).
- **Correctness**: I sent requests through `dispatch` at 5be8f4a:
  - `"1"`, `"20"`, and `"100"` returned 200.
  - `"101"` returned 422 with `"limit must be at most 100"`.
  - `"0"`, `"-5"`, `"many"`, and `"1e2"` returned 422 with the old messages.
  - If you do not send `limit`, the default is still 20.
  
  The `next_cursor` logic in `parcelpost/services/shipments.py:42` uses the same `limit` value, so the cap has no effect on it.
- **Intent vs implementation**: The PR also fixes something that the description does not mention. On `main`, `limit=99999999999999999999` returns a 500 because the integer is too large for SQLite. At 5be8f4a, the same request returns a 422. This change is an improvement and needs no action.
- **Testing**: The tests cover both sides of the boundary (`MAX_LIMIT` passes, `MAX_LIMIT + 1` fails). They also check the HTTP status and the error code through `dispatch`. pytest is not installed in this environment, so I did not run the suite with pytest. Instead, I ran the same checks directly against `dispatch` and `parse_limit`, and all of them passed.

## Open Questions
1. **Reject or cap?** At the moment, a client that sends `limit=101` or more gets a 200. After this PR, that client gets a 422. If an existing client sends a value above 100, this is a breaking change. Silently capping at 100 would not break those clients, and `next_cursor` would still work because it uses the capped value. To decide, check the access logs for `limit` values above 100 on `GET /customers/{id}/shipments`. If there are none, a 422 is the clearer behavior and you can merge the PR as it is.