# catalog

Product search and product detail pages for the bazaar marketplace.

Search ~300 req/s, detail ~500 req/s. Search p50 ~30 ms.

The semantic rescore beta is enabled only for allow-listed tenants and is off by default.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /v1/search` | Ranked product search with facets |
| `GET /v1/products/{id}` | Product detail payload |
| `GET /v1/suggest` | Autocomplete suggestions |
| `GET /v1/categories` | Category tree |
| `GET /v1/sellers/{id}` | Seller profile card |
| `GET /v1/feeds/products` | Paged product feed for the export job |

## Layout

- `search/` — text normalization, query construction, ranking, facets,
  and row hydration for the search path
- `products/` — repository, response views, media URLs, badges
- `clients/` — HTTP clients for sibling services
- `tests/` — unit tests for the pure ranking and text helpers

The service reads Postgres through `libs/dbkit` and keeps a short-lived
detail payload cache in Redis. Promoted placements reload hourly from
the promotions table.
