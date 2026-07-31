# gateway

Public API edge for the bazaar platform. Every external request enters
here: the service terminates client HTTP, attaches the caller's session,
applies per-tenant rate limits, writes access logs, and forwards the
request to the owning service over internal HTTP.

Production load: ~1,200 req/s peak, p50 8 ms; about 70% of requests carry
a session token.

## Request path

1. `middleware/accesslog.py` stamps a request id and records the outcome.
2. `middleware/cors.py` answers preflights and stamps origin headers.
3. `middleware/auth.py` attaches the caller identity via
   `handlers/session.py`, consulting the bounded token cache in
   `middleware/auth_cache.py` first.
4. `middleware/ratelimit.py` applies the per-tenant request budget from
   the rules file (about 2,000 tenant entries).
5. The route table in `routes.py` dispatches to a handler; proxy handlers
   call one sibling service per request through `clients/`.

## Layout

- `app.py` — application assembly and process entry point
- `routes.py` — the public route table
- `handlers/` — endpoint handlers (health, session, product and order
  proxies, tenant profile and usage)
- `clients/` — shared `httpx` clients for the auth and catalog services
- `middleware/` — the four-stage chain described above
- `responses.py` — response envelope and standard security headers
- `tests/` — unit checks with fakes; no network or disk access

Configuration is static in `config.py` and loads once at import time.
