# bazaar

A marketplace platform monorepo. Six services share three libraries and one
Postgres cluster; events move over Redis streams; services call each other
over HTTP inside one datacenter.

## Services and load

| Service | Role | Production load |
|---|---|---|
| `services/gateway` | Public API edge: routing, auth, rate limiting, access logs | ~1,200 req/s peak, p50 8 ms. About 70% of requests carry a session token. |
| `services/catalog` | Product search and detail pages | Search ~300 req/s, detail ~500 req/s. Search p50 ~30 ms. |
| `services/checkout` | Carts, order placement, order status, webhooks | ~800 orders/min, orders average 8 line items, order p50 budget 90 ms. Clients poll order status ~40 req/s at peak. |
| `services/pricing` | Consumes price/promo events from Redis streams, writes prices to Postgres | ~12,000 events/min (200/s). Each worker has about 5 ms per event at peak. |
| `services/inventory` | Stock reservation API for checkout | ~2,000 reservations/min. Exposes `/v1/reserve` and `/v1/reserve/batch` (up to 100 items per call). |
| `services/settlement` | Nightly payouts and fees batch | Runs once per day at 02:00 UTC over ~1.2M order rows. |

## Shared libraries

- `libs/dbkit` — async Postgres helpers: connection pool, query executor,
  batch inserts, SQL templates. Every service goes through dbkit.
- `libs/eventkit` — Redis stream producer/consumer and the event codec.
- `libs/commerce` — domain logic: money conversion and formatting, tax
  rates, SKU normalization, feature flags, audit records.

## Operational notes

- Postgres and Redis are in the same datacenter as the services. A simple
  indexed query round trip is about 0.5–2 ms; Redis operations are about
  0.2–1 ms; a sibling-service HTTP call is about 5–20 ms.
- The promoted-products list holds about 3,000 product ids and refreshes
  hourly.
- The promo ruleset snapshot in Redis is about 500 KB.
- The gateway rate-limit rules file holds about 2,000 tenant entries.
- A typical product row's `description_html` column is about 20 KB.
- The tax rule table ships with about 5,000 rows; the currency rate table
  lists 150 currencies.

## Tools

`tools/` holds the admin CLI, one-shot backfills, and the load generator.
These run manually or from CI, never in the serving path.
