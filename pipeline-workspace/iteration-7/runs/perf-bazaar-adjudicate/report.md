I have a complete picture. Writing the report.

# Performance Audit Report

## Summary

Bazaar is a marketplace monorepo. Six services share three libraries, one Postgres cluster, and Redis streams. The hot paths are the gateway edge (1,200 req/s), catalog search (300 req/s) and detail (500 req/s), the pricing consumer (200 events/s), and checkout order placement (13 orders/s against a 90 ms budget). The single best quick win is `services/catalog/search/rank.py:26`. That line tests each of 500 candidates against a 3,000-element **list**. A `frozenset` removes about 22 ms from a search path whose p50 is 30 ms, and the change is one line.

## Findings

### [P1] `attach_sellers` runs one query for each of the 25 page rows

- **Category**: I/O and Round Trips
- **Location**: `services/catalog/search/hydrate.py:16-27`
- **Hot path**: `routes.search_handler` at `services/catalog/routes.py:34` calls `hydrate.attach_sellers(page)` at `services/catalog/routes.py:49`. The handler runs about 300 times per second at peak.
- **Problem**: The loop at `hydrate.py:16` awaits one `fetch_one` for each row. The page holds `config.SEARCH_PAGE_SIZE` rows, which is 25 (`services/catalog/config.py:12`). The awaits are sequential, so the round trips do not overlap. Each `fetch_one` also acquires its own pooled connection (`libs/dbkit/executor.py:29`).
- **Evidence**:
```python
    for row in rows:
        seller = await fetch_one(
            "SELECT display_name, badge FROM sellers WHERE id = $1",
            (row["seller_id"],),
        )
```
- **Estimate**: This is an estimate. Assume a simple indexed query costs 0.5–2 ms, as `README.md:29` states. 25 sequential queries cost 25 × 0.5 ms = 12.5 ms at best and 25 × 2 ms = 50 ms at worst. Search p50 is 30 ms, so this step is 42% to 167% of the whole budget. At 300 req/s the service issues 25 × 300 = 7,500 seller queries per second. One `WHERE id = ANY($1)` call costs the same 0.5–2 ms. The saving is 12–48 ms per search and 7,200 fewer queries per second. The pool holds at most 20 connections (`libs/dbkit/config.py:12`), so 24 of the 25 acquisitions per search also disappear, which removes a throughput cap.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Collect the 25 `seller_id` values into a list. Run one query with `WHERE id = ANY($1)`. Index the result by id, then fill each row from the index. Measure the p50 of `/v1/search` and the `sellers` query count per second.

### [P1] `rerank` tests membership against a 3,000-element list for each of 500 candidates

- **Category**: Algorithms
- **Location**: `services/catalog/search/rank.py:26`
- **Hot path**: `routes.search_handler` at `services/catalog/routes.py:34` calls `rank.rank_page(candidates, promoted_ids, ...)` at `services/catalog/routes.py:48`. `rank_page` calls `rerank` at `services/catalog/search/rank.py:66`. The handler runs about 300 times per second at peak.
- **Problem**: `promoted.get_promoted_ids` returns a **list** (`services/catalog/search/promoted.py:32`). `README.md:31` states the list holds about 3,000 product ids. The `in` test on a list is a linear scan. `config.RANK_CANDIDATES` is 500 (`services/catalog/config.py:13`), so the loop runs 500 probes. Most product ids are not promoted, and a probe that fails scans all 3,000 elements.
- **Evidence**:
```python
    for candidate in candidates:
        score = float(candidate.get("score", 0.0))
        score += _RATING_WEIGHT * float(candidate.get("rating", 0.0))
        if candidate["product_id"] in promoted_ids:
```
- **Estimate**: This is an estimate. 500 candidates × 3,000 list elements = 1,500,000 integer comparisons for each search. CPython scans a list of `int` at about 10–20 ns per element, so the cost is 15–30 ms per search. Search p50 is 30 ms, so this one line accounts for most of it. A `frozenset` probe costs about one main memory reference, 50 ns; 500 probes cost about 25 µs. The saving is about 15–30 ms per search, a factor of about 900 on this step. At 300 req/s the saving is 4.5–9 s of CPU time per second, which is several full cores.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Build a `frozenset` in `promoted.get_promoted_ids` at reload time (`services/catalog/search/promoted.py:32`). Keep the list too if the order matters to a different caller. Do not build the set inside `rerank`, because that moves the 3,000-element cost into each request. Measure the p50 of `/v1/search`.

### [P1] `reserve_items` calls the single-item endpoint once for each of the 8 line items

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/reserve_step.py:21-35`
- **Hot path**: `create.place_order` at `services/checkout/orders/create.py:33` calls `reserve_items(order.id, priced)` at `services/checkout/orders/create.py:47`. `routes.create_order` at `services/checkout/routes.py:38` calls `place_order`. Order placement runs about 13 times per second at peak.
- **Problem**: The loop awaits one HTTP POST to `/v1/reserve` for each line item. The awaits are sequential. The inventory service already exposes `/v1/reserve/batch`, which accepts up to `RESERVE_BATCH_MAX` (100) SKUs in one call and one transaction (`services/inventory/routes.py:47` and `services/inventory/config.py:8`). The batch path is already written and already uses array parameters (`services/inventory/reservations/batch.py:47`). The checkout service does not call it.
- **Evidence**:
```python
    for item in items:
        response = await _client.post(
            INVENTORY_BASE_URL + "/v1/reserve",
            json={
                "order_id": order_id,
                "product_id": item.product_id,
                "quantity": item.quantity,
            },
        )
```
- **Estimate**: This is an estimate. `README.md:13` states an order averages 8 line items and the order p50 budget is 90 ms. `README.md:30` states a sibling-service HTTP call costs 5–20 ms. 8 sequential calls cost 40–160 ms. The low end already takes 44% of the budget, and the high end passes the whole budget. One batch call costs the same 5–20 ms. The saving is 35–140 ms per order. The change also cuts 8 inventory transactions to 1. Each transaction costs BEGIN, one row lock, one insert, one update, and COMMIT, which is 5 round trips (`services/inventory/reservations/create.py:44-59`). That removes about 35 round trips per order from the inventory service, or about 455 per second at 13 orders/s.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Replace the loop with one POST to `/v1/reserve/batch`. Send the whole item list in one body. The batch path fails as a unit, so the `_release_all` call at `services/checkout/orders/reserve_step.py:32` is no longer needed for a partial hold. Measure the p50 of `POST /v1/orders` and the request count per second on the inventory service.

### [P1] `find_affected_skus` scans a 2,000-element list for each of 30 candidate SKUs

- **Category**: Algorithms
- **Location**: `services/pricing/consumer/match.py:10-12`
- **Hot path**: `loop.run_loop` at `services/pricing/consumer/loop.py:32` calls `find_affected_skus(event, campaign_skus)` inside the per-event loop at `services/pricing/consumer/loop.py:53`. The worker handles 200 events per second and has about 5 ms per event.
- **Problem**: `campaigns.load_campaign_skus` returns a **list** (`services/pricing/campaigns.py:20`). `services/pricing/campaigns.py:3` states about 2,000 SKUs are on campaign at a time. `services/pricing/consumer/match.py:7` states an event carries up to about 30 candidate SKUs. The `in` test on a list is a linear scan, so each event does up to 30 full scans of 2,000 strings. The result feeds only the `campaign_hits` counter in the batch log line at `services/pricing/consumer/loop.py:62`.
- **Evidence**:
```python
    affected = []
    for sku in event.candidate_skus:
        if sku in campaign_skus:
            affected.append(sku)
    return affected
```
- **Estimate**: This is an estimate. 30 candidate SKUs × 2,000 list elements = 60,000 string comparisons per event. A short-string comparison inside a list scan costs about 30–50 ns, so the cost is 1.8–3.0 ms per event. The per-event budget is 5 ms, so this step takes 36% to 60% of it. At 200 events/s the cost is 360–600 ms of CPU time per second. A `frozenset` probe costs about one main memory reference, 50 ns; 30 probes cost about 1.5 µs. The saving is about 1.8–3.0 ms per event, a factor of about 1,500 on this step.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Return a `frozenset` from `campaigns.load_campaign_skus`, or convert the roster once at `services/pricing/consumer/loop.py:34`. `roster_digest` at `services/pricing/campaigns.py:45` reads `skus[0]`, so keep the list for that call or change the digest to report only the count. Measure the p99 in the batch log line at `services/pricing/consumer/loop.py:64`.

### [P1] `apply_price_update` reads and parses the 500 KB promo snapshot for each event

- **Category**: Redundant Work
- **Location**: `services/pricing/consumer/apply.py:22` and `services/pricing/consumer/rules.py:7-13`
- **Hot path**: `loop.run_loop` at `services/pricing/consumer/loop.py:32` awaits `apply_price_update(event)` inside the per-event loop at `services/pricing/consumer/loop.py:54`. The worker handles 200 events per second and has about 5 ms per event.
- **Problem**: `load_active_promos` does one Redis GET and one `json.loads` on every call. `README.md:33` states the snapshot is about 500 KB. The snapshot changes once per hour, because `snapshots.publish_snapshot` runs hourly from the admin CLI (`services/pricing/snapshots.py:4` and `services/pricing/README.md:12`). The worker therefore re-reads and re-parses the same 500 KB up to 200 times per second.
- **Evidence**:
```python
async def load_active_promos(redis_client):
    """Returns the active promo ruleset keyed by SKU.

    The snapshot is about 500 KB. Called before each event is applied.
    """
    raw = await redis_client.get(PROMO_SNAPSHOT_KEY)
```
- **Estimate**: This is an estimate. A Redis operation costs 0.2–1 ms (`README.md:30`). The 500 KB payload crosses the socket and Python copies it; a read of 1 MB from memory costs 64 µs, so 500 KB costs about 32 µs per copy. `json.loads` of 500 KB with a few thousand objects costs about 3–6 ms and builds a fresh dict that becomes garbage at once. The total is 3.5–7 ms per event against a 5 ms budget, so the parse alone can exceed the whole budget. At 200 events/s the worker also pulls 200 × 500 KB = 100 MB/s from Redis. A cached parse costs one dict lookup, about 50 ns. The saving is 3.5–7 ms per event and about 100 MB/s of Redis traffic.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Cache the parsed dict in the module. Refresh it on a timer, or watch a version key that `publish_snapshot` stamps. `services/pricing/snapshots.py:47` already runs `STAMP_QUERY` against `promo_meta`, and `snapshot_age_seconds` at `services/pricing/snapshots.py:30` already reads that stamp. Reuse that stamp as the cache key. Measure the p99 in the batch log line and the Redis network byte rate.

### [P1] `ratelimit.check` opens and parses the 2,000-entry rules file on every request

- **Category**: Redundant Work
- **Location**: `services/gateway/middleware/ratelimit.py:15-22`
- **Hot path**: `ratelimit.middleware` at `services/gateway/middleware/ratelimit.py:53` calls `check(tenant_id)` at `services/gateway/middleware/ratelimit.py:56`. `app.create_app` installs the middleware for every route at `services/gateway/app.py:29`. The gateway peaks at 1,200 req/s.
- **Problem**: `check` opens the rules file, reads it, and parses the JSON on each call. `README.md:34` states the file holds about 2,000 tenant entries. The function then reads one key and drops the rest. `services/gateway/config.py:3` states configuration loads once at import time, but this file does not follow that rule. The `open` call is also synchronous, so it blocks the event loop and delays every other request in flight.
- **Evidence**:
```python
def check(tenant_id):
    """Returns the per-minute budget for a tenant. Runs for each request."""
    with open(RATE_RULES_PATH) as f:
        rules = json.load(f)
    rule = rules.get(tenant_id)
```
- **Estimate**: This is an estimate. Assume each of the 2,000 entries costs about 45 bytes of JSON text, which gives a file of about 90 KB. The page cache normally serves the read, so count about 50 µs for the syscalls and the copy; a cold read costs 23 random 4 KB SSD reads at 20 µs each, which is 460 µs. `json.load` of 90 KB with 2,000 nested objects costs about 0.6–1.2 ms. The total is 0.7–1.3 ms per request. At 1,200 req/s that is 840–1,560 ms of CPU time per second, which is more than one full core. p50 is 8 ms, so the parse is 9–16% of p50 before the event loop stall. One in-memory dict lookup costs about 50 ns. The saving is about 1 ms per request and about 1.2 s of CPU time per second.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Load the rules once at import into a module dict, beside the other values in `services/gateway/config.py`. Reload on SIGHUP or on a timer if operations need edits without a restart. `libs/dbkit/notify.py` already carries a LISTEN/NOTIFY path for reload signals. Measure the gateway p50 and the requests per second one worker sustains.

### [P1] `attach_session` makes a blocking HTTP call inside the async handler

- **Category**: I/O and Round Trips
- **Location**: `services/gateway/handlers/session.py:28-31`
- **Hot path**: `auth.middleware` at `services/gateway/middleware/auth.py:31` awaits `attach_session(request)` at `services/gateway/middleware/auth.py:41` and at `services/gateway/middleware/auth.py:45`. `app.create_app` installs the middleware for every route at `services/gateway/app.py:29`. The gateway peaks at 1,200 req/s, and `README.md:11` states about 70% of requests carry a session token.
- **Problem**: `urllib.request.urlopen` is synchronous. It stops the whole event loop until the auth service answers. No other request progresses during that time. The token cache absorbs most calls, but every miss stalls the process. The same package already holds an `httpx.AsyncClient` with a connection pool at `services/gateway/clients/auth_client.py:11`, and that client already calls the same auth service.
- **Evidence**:
```python
    payload = json.dumps({"token": token}).encode("utf-8")
    raw = urllib.request.urlopen(
        AUTH_BASE_URL + "/v1/verify", data=payload, timeout=REQUEST_TIMEOUT_S
    )
    body = json.loads(raw.read())
```
- **Estimate**: This is an estimate. `SESSION_CACHE_TTL_S` is 300 and `SESSION_CACHE_MAX` is 10,000 (`services/gateway/config.py:24-25`). Assume a steady set of 10,000 active tokens. Each token then re-verifies every 300 s, which gives at least 10,000 / 300 ≈ 33 misses per second. A sibling-service call costs 5–20 ms (`README.md:30`). 33 misses × 5 ms = 165 ms of event loop stall per second; at 20 ms it is 660 ms per second. The gateway therefore loses 17% to 66% of its wall clock to a stall during which every other request queues. p50 is 8 ms, so one 20 ms stall alone is 2.5 times p50. A non-blocking call keeps the 5–20 ms cost on that one request and frees the loop for the rest.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Add a `verify_token` function to `services/gateway/clients/auth_client.py` that posts to `/v1/verify` on the existing `httpx.AsyncClient`. Await it from `attach_session`. Also add single-flight suppression, so 100 concurrent requests with one cold token make one upstream call and not 100. Measure the gateway p99 and the event loop lag.

### [P1] `fetch_all` serializes every result set to JSON for a debug log that is off

- **Category**: Observability Overhead
- **Location**: `libs/dbkit/executor.py:23`
- **Hot path**: Every service reads Postgres through this function (`README.md:20-21`). The hottest caller is `query.candidate_rows` at `services/catalog/search/query.py:69`, which `routes.search_handler` calls about 300 times per second (`services/catalog/routes.py:47`). The heaviest single caller is `cron.run_once` at `services/settlement/cron.py:43`.
- **Problem**: `json.dumps(rows, default=str)` sits in the argument list of `logger.debug`. Python evaluates an argument before the call, so the log level never stops the work. Every service sets the level to INFO (`services/gateway/app.py:41`, `services/catalog/app.py:44`, `services/pricing/worker.py:40`), so the encoder runs and the string goes straight to garbage. `default=str` adds one Python-level call for every `date` and `Decimal` value in the result.
- **Evidence**:
```python
    rows = [dict(record) for record in records]
    logger.debug("rows=%s", json.dumps(rows, default=str))
    return rows
```
- **Estimate**: This is an estimate. `config.RANK_CANDIDATES` is 500 and the query selects 8 columns (`services/catalog/config.py:13` and `services/catalog/search/query.py:11`), so one search encodes 500 × 8 = 4,000 key and value pairs. The C encoder costs about 200–400 ns per pair with the per-dict overhead, which gives 0.8–1.6 ms per search, plus a 60–100 KB string that becomes garbage at once. At 300 req/s the cost is 240–480 ms of CPU time per second and 18–30 MB/s of garbage. The nightly settlement pass is far worse: `services/settlement/cron.py:43` fetches about 1.2M order rows in one call (`README.md:16`), so the encoder must handle 1.2M × 6 = 7.2M values and hold a single string of about 200–400 MB. That risks an out-of-memory failure on the nightly run. A level guard costs one integer comparison, about 5 ns. The saving is 0.8–1.6 ms per search and the whole settlement string.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Wrap the call in `if logger.isEnabledFor(logging.DEBUG):`. Also cap the output, for example to the first 20 rows plus a count, so a debug session on the settlement path does not build a 400 MB string. Measure the search p50 and the peak resident memory of the settlement process.

### [P1] `rate_for` scans a 5,100-row list for each tax lookup

- **Category**: Algorithms
- **Location**: `libs/commerce/tax.py:29-32`
- **Hot path**: Two callers. `apply.apply_price_update` calls `rate_for(event.region, event.category)` at `services/pricing/consumer/apply.py:32`, inside the per-event loop at `services/pricing/consumer/loop.py:54`; that runs 200 times per second. `pricing_step.price_items` calls `rate_for(buyer_region, item.category)` at `services/checkout/orders/pricing_step.py:29`, inside the per-item loop at `services/checkout/orders/pricing_step.py:24`; that runs 8 times per order at 13 orders/s.
- **Problem**: `_RULES` is a list of tuples built at import from every region and category pair (`libs/commerce/tax.py:16-24`). `libs/commerce/regions.py` holds 150 regions and `libs/commerce/catalog_types.py` holds 34 categories, so the list holds 150 × 34 = 5,100 rows. `README.md:36` states the table ships with about 5,000 rows. `rate_for` walks the list and compares two strings per row. A dict keyed by the pair answers the same question in one lookup.

  The two callers make the scan worse. `REGIONS` holds uppercase codes such as `"US"`. The pricing event region defaults to `"us"` in lowercase (`services/pricing/consumer/decode.py:14`), and the checkout supported regions are also lowercase (`services/checkout/config.py:26`). No row ever matches either caller, so every call runs the full 5,100-row scan and returns `DEFAULT_RATE`. The fix must also settle which case is canonical, because the current code charges the default rate everywhere.
- **Evidence**:
```python
def rate_for(region, category) -> float:
    """Return the tax rate for a region and product category."""
    for rule_region, rule_category, rate in _RULES:
        if rule_region == region and rule_category == category:
            return rate
    return DEFAULT_RATE
```
- **Estimate**: This is an estimate. Each loop pass unpacks a 3-tuple and compares one or two short strings, about 80–120 ns. A full scan of 5,100 rows costs about 410–610 µs; an average hit at row 2,550 costs about 205–305 µs. Because neither caller matches, both pay the full scan. For the pricing worker that is about 510 µs of the 5 ms per-event budget, or 10%, and 102 ms of CPU time per second at 200 events/s. For checkout that is 8 × 510 µs ≈ 4.1 ms of the 90 ms order budget, or 4.6%. A dict lookup costs about one main memory reference, 50 ns. The saving is about 510 µs per lookup, a factor of about 10,000.
- **Impact**: High
- **Effort**: Small
- **Suggested fix**: Build `_RATES = {(region, category): rate for ...}` at import, beside the current list. Return `_RATES.get((region, category), DEFAULT_RATE)`. Keep `_RULES` if `rule_count` at `libs/commerce/tax.py:40` needs it. Decide the canonical case for a region code and normalize at the two call sites. Measure the p99 in the pricing batch log line and the p50 of `POST /v1/orders`. Check the stored `tax_rate` values before and after, because the correct rates will differ from `DEFAULT_RATE`.

### [P2] `dispatch` delivers webhooks one at a time on the order placement path

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/webhooks/notify.py:26-49`
- **Hot path**: `create.place_order` awaits `dispatch("order.placed", ...)` at `services/checkout/orders/create.py:52`. `routes.create_order` at `services/checkout/routes.py:38` awaits `place_order`, so the client waits for every delivery. Order placement runs about 13 times per second at peak.
- **Problem**: The loop at `services/checkout/webhooks/notify.py:36` awaits each subscriber in turn. The subscriber endpoints are independent, so nothing forces the serial order. `WEBHOOK_TIMEOUT_S` is 5 seconds per subscriber (`services/checkout/config.py:9`), and the timeouts add up across the loop. One slow subscriber therefore holds the order response open.
- **Evidence**:
```python
    urls = await subscribers_for(topic)
    delivered = 0
    failed = []
    for url in urls:
        try:
            await send_order_event(url, event)
```
- **Estimate**: This is an estimate. Assume 5 subscribers. With healthy endpoints at 20 ms each the loop adds 100 ms, which already passes the 90 ms order p50 budget. With 5 subscribers at the 5 s timeout the loop adds 25 s to one request. `asyncio.gather` cuts the wall clock to the slowest subscriber: 20 ms in the healthy case and 5 s in the worst case. A move off the request path removes the cost from the budget entirely. The saving is 80 ms per order in the healthy case and up to 25 s in the worst case.
- **Impact**: Medium
- **Effort**: Medium
- **Suggested fix**: Wrap each delivery in a task and await them with `asyncio.gather(..., return_exceptions=True)`. Then move the whole fan-out off the request path, either onto a Redis stream through `libs/eventkit/publisher.py` or into a background task. Note the trade-off: `dispatch` returns the successful delivery count today, and a background fan-out cannot return it to the caller. Measure the p99 of `POST /v1/orders` with one deliberately slow subscriber.

### [P2] `send_order_event` builds a new HTTP client and connection pool for each delivery

- **Category**: Allocation and Copies
- **Location**: `services/checkout/webhooks/notify.py:19-20`
- **Hot path**: `dispatch` calls `send_order_event(url, event)` once per subscriber at `services/checkout/webhooks/notify.py:38`. `create.place_order` awaits `dispatch` at `services/checkout/orders/create.py:52` and `create.cancel_order` awaits it at `services/checkout/orders/create.py:71`. Order events run about 13 times per second at peak.
- **Problem**: `async with httpx.AsyncClient(...)` builds a fresh client and a fresh connection pool on each call, then closes both. Each delivery therefore opens a new TCP connection, and an https endpoint also needs a new TLS handshake. No connection is ever reused. Three other modules in the same service already hold one module-level client: `services/checkout/orders/reserve_step.py:11`, `services/checkout/clients/inventory_client.py:12`, and `services/settlement/banking.py:16`.
- **Evidence**:
```python
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
        response = await client.post(url, json=event)
```
- **Estimate**: This is an estimate. A TCP handshake costs one round trip and a TLS handshake costs about two more, plus the key exchange. Subscriber endpoints are customer-owned and sit outside the datacenter, so use the California-to-Netherlands figure of 150 ms per round trip: three round trips cost about 450 ms per delivery. A near endpoint at 5–20 ms per round trip still costs 15–60 ms. A kept-alive pooled connection pays this once and then reuses it. Assume 5 subscribers and 13 orders/s, which gives 65 deliveries per second. The saving is 15–450 ms per delivery, and it removes 65 handshakes per second. This finding compounds with the sequential loop above, because each handshake also holds the order response open.
- **Impact**: Medium
- **Effort**: Small
- **Suggested fix**: Move the client to module level, as the three sibling modules do. Close it from the application cleanup hook. Measure the p50 of one webhook delivery and the new-connection count on the subscriber side.

### [P2] `LatencyWindow.record` sorts the whole 2,048-sample window for each event

- **Category**: Redundant Work
- **Location**: `services/pricing/consumer/stats.py:16-19`
- **Hot path**: `loop.run_loop` calls `tracker.record(...)` inside the per-event loop at `services/pricing/consumer/loop.py:56`. The worker handles 200 events per second and has about 5 ms per event.
- **Problem**: `record` sorts the entire window to refresh one percentile. `STATS_WINDOW` is 2,048 (`services/pricing/config.py:23`). The loop reads `p99_ms` once per batch, at `services/pricing/consumer/loop.py:64`, and a batch holds up to `STREAM_READ_COUNT` (100) events (`services/pricing/config.py:20`). The code therefore sorts 100 times more often than it reads the result.
- **Evidence**:
```python
    def record(self, value):
        """Adds one sample and refreshes the p99. Called once per event."""
        self._window.append(value)
        ordered = sorted(self._window)
        rank = max(0, int(len(ordered) * 0.99) - 1)
        self.p99_ms = ordered[rank]
```
- **Estimate**: This is an estimate. `sorted` on a 2,048-element deque copies the deque into a list and then sorts it. The sort costs about 2,048 × log2(2,048) = 22,500 comparisons at roughly 20–30 ns each, which is 0.45–0.68 ms, plus one 2,048-element list allocation of about 16 KB per event. That is 9% to 14% of the 5 ms per-event budget. At 200 events/s the cost is 90–136 ms of CPU time per second and 3.2 MB/s of allocation. A sort inside `summary()` runs twice per second instead of 200 times, so it costs 0.9–1.4 ms per second. The saving is about 0.45–0.68 ms per event, a factor of about 100 on this step.
- **Impact**: Medium
- **Effort**: Small
- **Suggested fix**: Let `record` append the sample only. Move the sort and the rank arithmetic into `summary()` at `services/pricing/consumer/stats.py:21`, which the batch log line already calls. Keep `p99_ms` as a property so the current callers still work. Measure the p99 in the batch log line and the events per second one worker sustains.

### [P2] `get_product` uses `SELECT *` and reads the 20 KB description column that the view drops

- **Category**: Data Layout
- **Location**: `services/catalog/products/repo.py:14-19`
- **Hot path**: `routes.detail_handler` at `services/catalog/routes.py:57` calls `repo.get_product(product_id)` at `services/catalog/routes.py:66`, after a Redis cache miss. The handler runs about 500 times per second, and `DETAIL_CACHE_TTL_S` is 60 (`services/catalog/config.py:15`).
- **Problem**: The query selects every column. `README.md:35` states a typical product row's `description_html` column is about 20 KB. `views.detail_view` reads exactly 8 fields and none of them is `description_html` (`services/catalog/products/views.py:22-32`). Every uncached detail read therefore moves about 20 KB that the response drops. Postgres must also read the TOAST pages that hold the large column. The service already names its 8 listing columns in a constant at `services/catalog/products/repo.py:23`, and both other repository functions use it.
- **Evidence**:
```python
    return await fetch_one(
        "SELECT * FROM products WHERE id = $1",
        (product_id,),
    )
```
- **Estimate**: This is an estimate. Assume 20% of the 500 req/s miss the 60-second cache, which gives 100 uncached reads per second. Each read moves an extra 20 KB, so the total is 2 MB/s of database traffic. asyncpg decodes 20 KB of text in about 20–40 µs. The extra wire time and the TOAST page reads add roughly 0.2–0.5 ms to a query whose round trip is 0.5–2 ms, so the query cost grows by 10% to 100%. The saving is about 0.3 ms per uncached read and 2 MB/s of database traffic.
- **Impact**: Medium
- **Effort**: Small
- **Suggested fix**: Name the 8 columns that `detail_view` reads, or reuse `_LISTING_COLUMNS` at `services/catalog/products/repo.py:23`. Check first whether a different caller needs `description_html`; grep shows none does. Measure the p50 of `/v1/products/{id}` on a cache miss and the bytes sent by the `products` query.

### [P2] `order_status_view` reads and parses the 25 KB items blob to compute two numbers

- **Category**: Data Layout
- **Location**: `services/checkout/orders/summary.py:8` and `services/checkout/orders/summary.py:19-22`
- **Hot path**: `routes.get_order_status` at `services/checkout/routes.py:48` calls `summary.order_status_view(order_id)` at `services/checkout/routes.py:50`. `README.md:13` states clients poll this endpoint about 40 times per second at peak.
- **Problem**: The query selects `items_json`, and `services/checkout/orders/models.py:3` states that column holds about 25 KB per order. The handler parses the whole blob to produce two values: `item_count` and `total_cents`. The `orders` table already stores `total_cents` as its own column (`services/checkout/orders/txn.py:6`), so that value needs no parse at all. The line items also live in the `order_items` table (`services/checkout/orders/txn.py:11`), so a count is available without the blob.
- **Evidence**:
```python
_STATUS_QUERY = "SELECT status, items_json, updated_at FROM orders WHERE id = $1"
...
    items = json.loads(row["items_json"])
    total_cents = sum(
        entry["unit_price_cents"] * entry["quantity"] for entry in items
    )
```
- **Estimate**: This is an estimate. `json.loads` of 25 KB with about 8 nested objects costs about 100–200 µs. The 25 KB also crosses the wire: at 40 req/s that is 1 MB/s of database traffic, and the transfer plus the decode add roughly 0.3–0.5 ms to a query whose round trip is 0.5–2 ms. The CPU cost is 4–8 ms per second. A query that selects `status`, `total_cents`, `item_count`, and `updated_at` moves about 60 bytes and needs no parse. The saving is about 0.3–0.5 ms per poll and 1 MB/s of database traffic.
- **Impact**: Medium
- **Effort**: Small
- **Suggested fix**: Select `total_cents` from the column. Add an `item_count` column to `orders` and set it at write time in `services/checkout/orders/txn.py`, or run `SELECT count(*) FROM order_items WHERE order_id = $1`. Prefer the stored column, because it keeps the endpoint at one query. Measure the p50 of `GET /v1/orders/{order_id}` and the bytes sent by the `orders` query.

### [P2] `money.convert` parses a 2.9 KB JSON rate table on each call

- **Category**: Redundant Work
- **Location**: `libs/commerce/money.py:53`
- **Hot path**: `apply.apply_price_update` calls `convert` three times per event, at `services/pricing/consumer/apply.py:25`, `services/pricing/consumer/apply.py:29`, and `services/pricing/consumer/apply.py:31`. That loop runs 200 times per second (`services/pricing/consumer/loop.py:54`). `pricing_step.price_items` calls `convert` once per line item at `services/checkout/orders/pricing_step.py:28`, which is 8 times per order at 13 orders/s.
- **Problem**: `_RATES_JSON` is a string constant of about 150 currency entries in roughly 2.9 KB of text (`libs/commerce/money.py:17-48`). `convert` parses it on every call and builds a fresh 150-entry dict that becomes garbage at once. The value never changes; `libs/commerce/money.py:15` states the rates refresh with each library release. The same module already builds its `Decimal` quantum constants once at import (`libs/commerce/money.py:70-72`) and the comment at `libs/commerce/money.py:66` states the intent.
- **Evidence**:
```python
def convert(amount_cents, from_ccy, to_ccy) -> int:
    """Convert an integer minor-unit amount between currencies."""
    rates = json.loads(_RATES_JSON)
```
- **Estimate**: This is an estimate. `json.loads` of 2.9 KB with 150 float keys costs about 15–30 µs and allocates a dict of about 9 KB. The pricing worker pays 3 × 15–30 µs = 45–90 µs per event, which is 1% to 2% of the 5 ms budget, and 9–18 ms of CPU time per second at 200 events/s. Checkout pays 8 × 15–30 µs = 120–240 µs per order. A module-level dict lookup costs about 50 ns, so the saving is about 15–30 µs per call, a factor of about 400.
- **Impact**: Medium
- **Effort**: Small
- **Suggested fix**: Parse `_RATES_JSON` once at import into a module constant, next to `_CENT` at `libs/commerce/money.py:70`. Read that constant from `convert`. Measure the p99 in the pricing batch log line.

### [P3] `facet_counts` re-derives the field kind for each of 2,000 cells

- **Category**: Redundant Work
- **Location**: `services/catalog/search/facets.py:22-26` and `services/catalog/search/facets.py:29-40`
- **Hot path**: `routes.search_handler` calls `facets.facet_counts(candidates)` at `services/catalog/routes.py:53`. The candidate set holds up to `config.RANK_CANDIDATES` (500) rows. The handler runs about 300 times per second at peak.
- **Problem**: The nested loop at `services/catalog/search/facets.py:36` runs 500 rows × 4 fields = 2,000 cells. Each cell calls `_facet_value`, which compares `field == "rating_band"` on every cell although only one of the four fields needs it. For that one field `rating_band` scans up to 4 band edges and builds a new string with `%`. Each cell then does three dict operations, because the code tests `if value not in counts[field]` and then reads and writes the same key.
- **Evidence**:
```python
    for row in rows:
        for field in FACET_FIELDS:
            value = _facet_value(row, field)
            if value not in counts[field]:
                counts[field][value] = 0
            counts[field][value] = counts[field][value] + 1
```
- **Estimate**: This is an estimate. Each cell costs about 300–500 ns: one function call, one string comparison, one `str()` or one band scan, and three dict operations. 2,000 cells cost 0.6–1.0 ms per search, which is 2% to 3% of the 30 ms search p50. At 300 req/s the cost is 180–300 ms of CPU time per second. A `dict.get(value, 0) + 1` pattern removes one of the three dict operations, and a pre-built band tuple removes the per-row string build. The saving is about 0.4–0.7 ms per search.
- **Impact**: Low
- **Effort**: Small
- **Suggested fix**: Handle the `rating_band` field outside the field loop. Replace the three-operation counter update with `bucket[value] = bucket.get(value, 0) + 1`, or use `collections.Counter`. Return the band strings from a pre-built tuple indexed by the band, so no cell allocates a string. Measure the search p50.

### [P3] `facets.summary` sorts every facet value to return the leading 8

- **Category**: Algorithms
- **Location**: `services/catalog/search/facets.py:44-47`
- **Hot path**: `routes.search_handler` calls `facets.summary(counts)` at `services/catalog/routes.py:55`. The handler runs about 300 times per second at peak.
- **Problem**: The code sorts all values of each facet and then keeps the first `TOP_VALUES` (8). A full sort is more work than a top-k selection. The `seller_id` facet over 500 candidate rows can hold up to 500 distinct values.
- **Evidence**:
```python
        pairs = sorted(
            counts[field].items(), key=lambda kv: kv[1], reverse=True
        )
        shaped[field] = [
            {"value": value, "count": counts[field][value]}
            for value, _ in pairs[:TOP_VALUES]
        ]
```
- **Estimate**: This is an estimate. Assume the `seller_id` facet holds 500 distinct values and the other three hold far fewer. `sorted` with a lambda key calls the lambda 500 times at about 60 ns each and then runs about 4,500 comparisons at about 20 ns each, which is 50–80 µs. The four facets together cost 100–200 µs per search. `heapq.nlargest(8, ...)` runs in O(N log 8) and costs about 20–30 µs. The saving is about 80–170 µs per search, which is under 1% of the 30 ms p50, and 24–51 ms of CPU time per second at 300 req/s.
- **Impact**: Low
- **Effort**: Small
- **Suggested fix**: Replace `sorted(...)[:TOP_VALUES]` with `heapq.nlargest(TOP_VALUES, counts[field].items(), key=...)`. Use `operator.itemgetter(1)` instead of the lambda. Measure the search p50.

### [P3] `log_request` serializes the access record for a debug log that is off

- **Category**: Observability Overhead
- **Location**: `services/gateway/middleware/accesslog.py:20`
- **Hot path**: `accesslog.middleware` at `services/gateway/middleware/accesslog.py:24` calls `log_request(...)` at `services/gateway/middleware/accesslog.py:30`. `app.create_app` installs the middleware first in the chain for every route (`services/gateway/app.py:27`). The gateway peaks at 1,200 req/s.
- **Problem**: `json.dumps(context)` sits in the argument list of `logger.debug`, so Python evaluates it before the call. `app.main` sets the level to INFO at `services/gateway/app.py:41`, so the record never reaches a handler and the encoded string goes to garbage. The same INFO level also means the access log never reaches the log shipper, which `services/gateway/README.md:11` treats as a live feature. That is a behaviour question for the operator, not a speed question.
- **Evidence**:
```python
def log_request(context):
    """Emits one access record. Runs for each completed request."""
    logger.debug("request %s", json.dumps(context))
```
- **Estimate**: This is an estimate. The context dict holds 7 keys with short string and number values. `json.dumps` on it costs about 5–10 µs. At 1,200 req/s the cost is 6–12 ms of CPU time per second, which is about 0.1% of the 8 ms p50. A level guard costs one integer comparison, about 5 ns. The saving is 5–10 µs per request. The saving is small; fix it while the file is open for the P1 change in the same middleware chain.
- **Impact**: Low
- **Effort**: Small
- **Suggested fix**: Guard the call with `if logger.isEnabledFor(logging.DEBUG):`, or pass the dict as a lazy argument and let a JSON formatter encode it. Decide separately whether the access record belongs at INFO. Measure the gateway p50.

### [P3] `carts/repo` runs two separate statements for one cart edit

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/carts/repo.py:44-46`, `services/checkout/carts/repo.py:50-52`, and `services/checkout/carts/repo.py:56-58`
- **Hot path**: `routes.put_cart_item` calls `repo.set_item(...)` at `services/checkout/routes.py:93`. `routes.delete_cart_item` calls `repo.remove_item(...)` at `services/checkout/routes.py:110`. `create.place_order` calls `repo.clear_cart(...)` at `services/checkout/orders/create.py:51`. `services/checkout/carts/repo.py:26` states cart access runs a few dozen times per second.
- **Problem**: Each function calls `execute` twice. Every `execute` acquires its own pooled connection and makes its own round trip (`libs/dbkit/executor.py:34-37`). The second statement only stamps `updated_at`. No transaction wraps the pair, so a failure between the two leaves the row changed and the timestamp stale.
- **Evidence**:
```python
async def set_item(cart_id, product_id, category, quantity):
    """Adds or updates one cart line. Called once per cart edit."""
    await execute(_UPSERT_ITEM, (cart_id, product_id, category, quantity))
    await execute(_TOUCH_CART, (cart_id,))
```
- **Estimate**: This is an estimate. One indexed statement costs 0.5–2 ms (`README.md:29`). The second round trip therefore adds 0.5–2 ms per edit. Assume 24 cart edits per second, which matches "a few dozen times per second". The total waste is 12–48 ms of wall clock per second and 24 extra pool acquisitions per second. The pool holds at most 20 connections (`libs/dbkit/config.py:12`). The saving is 0.5–2 ms per edit.
- **Impact**: Low
- **Effort**: Small
- **Suggested fix**: Combine each pair into one statement with a CTE, or wrap the pair in one `txn.transaction()` scope so both share one connection and one round trip group. The transaction form also fixes the stale-timestamp window. Measure the p50 of `PUT /v1/carts/{cart_id}/items`.

### [P3] `audit_context` walks the whole call stack for each audit record

- **Category**: Redundant Work
- **Location**: `libs/commerce/audit.py:26-40`
- **Hot path**: `create.place_order` calls `write_audit(audit_context())` at `services/checkout/orders/create.py:50`, once per order at 13 orders/s. `ledger.write_ledger` calls it once per batch at `services/settlement/payouts/ledger.py:48`, where `LEDGER_BATCH_SIZE` is 1,000 (`services/settlement/config.py:17`).
- **Problem**: `traceback.extract_stack()` builds one `FrameSummary` for every frame on the stack and asks `linecache` for each frame's source line. The function needs only the nearest frame outside the package, which `sys._getframe` gives directly. `datetime.now(timezone.utc).isoformat()` also runs per record, and `json.dumps(..., sort_keys=True)` sorts the keys of every record.
- **Evidence**:
```python
    frames = traceback.extract_stack()
    caller = "unknown"
    # The nearest frame outside this package names the caller tag.
    for frame in reversed(frames[:-1]):
        if _OWN_PATH not in frame.filename:
```
- **Estimate**: This is an estimate. An aiohttp handler stack is about 20–30 frames deep. `extract_stack` allocates one `FrameSummary` per frame and does one `linecache` dict lookup per frame, which costs about 50–150 µs after the source files are cached. At 13 orders/s the cost is 0.7–2 ms of CPU time per second, and one call is about 0.15% of the 90 ms order budget. `sys._getframe(1)` plus one `os.path.basename` call costs about 1 µs. The saving is about 50–150 µs per record, a factor of about 100.
- **Impact**: Low
- **Effort**: Small
- **Suggested fix**: Walk `sys._getframe(1)` and follow `f_back` until the filename leaves the package. Stop at the first match instead of a full stack capture. Measure the p50 of `POST /v1/orders`.

## Cold-Code Notes

- `tools/backfill_sellers.py:44-62` — `match_legacy_rows` runs a nested loop over legacy rows × current rows. The work is O(N²) where a dict keyed by email and a dict keyed by (display_name, signup_date) give O(N). This is a one-shot migration script.
- `services/settlement/payouts/rollup.py:32` — `top_sellers` sorts all 1.2M order rows to keep the leading `TOP_SELLERS_LIMIT` (50). `heapq.nlargest(50, ...)` does the same work without the full sort and saves about 0.5–1 s per nightly run.
- `services/settlement/reports/daily.py:20-22` — `render_report` calls `summarize_fees(conn, day)` three times, so the same GROUP BY query runs three times per nightly run. Call it once and pass the rows to the three helpers.
- `services/settlement/reports/render.py:9` — `format_money_rows` builds a `decimal.Context` inside the row loop. The context is loop-invariant. The row count equals the plan count, which `services/settlement/payouts/fees.py:10` shows is 3.
- `libs/commerce/flags.py:36` — `load_flag_file` calls `re.compile` inside the line loop. The file runs once at import over about 30 lines (`libs/commerce/flags.py:4`).
- `libs/commerce/skuid.py:29-38` — `normalize_sku` builds the `str.maketrans` table on each call. Only `services/catalog/products/repo.py:32` calls it, and that function serves the admin CLI.
- `libs/dbkit/prepare.py:24-29` — `render_sql` runs one full `re.sub` pass over the template for each parameter, so the template is scanned N times for N parameters. No serving-path caller uses this function.
- `libs/dbkit/batch.py:19-25` — `build_values_clause` grows the clause string with `+=` over up to `FLUSH_ROWS` (500) rows. CPython's in-place string optimization usually keeps this linear, because the local variable holds the only reference. A list plus `"".join` removes the risk.
- `services/catalog/search/spelling.py:22-30` — `_single_edits` yields about 54 candidate strings per character position. `correct` runs only for searches that return zero candidates.
- `services/catalog/search/semantic.py:33-42` — `semantic_rescore` compares every candidate against every reference vector, which is O(candidates × vectors × 64) with a pure Python inner loop. `config.SEMANTIC_RESCORE` is `False` (`services/catalog/config.py:14`), and `services/catalog/README.md:7` states the beta is off by default, so the code does not run. It becomes a P1 the day a tenant turns it on: 500 candidates × 64 dimensions is 32,000 float multiplications per reference vector.

## Not Found

- **Allocation and Copies** — almost nothing to report. A grep for `deepcopy` and for `json.loads(json.dumps(...))` returned no hits anywhere in `services/` or `libs/`. The one real finding in this category is the client-per-call in `services/checkout/webhooks/notify.py:19`, listed as P2 above.
- **Data Layout** — the codebase already keys its lookup tables well. `libs/commerce/regions.py`, `libs/commerce/catalog_types.py`, `libs/commerce/currencies.py`, `services/catalog/search/stopwords.py`, `services/checkout/carts/rules.py`, and `services/checkout/orders/models.py` all use `frozenset` or `dict` for membership. The two exceptions are `services/catalog/search/rank.py:26` and `libs/commerce/tax.py:29`, both P1 findings above. The one layout finding is `SELECT *` at `services/catalog/products/repo.py:17`, listed as P2 above.
- **Dismissed — `re.compile` inside `normalize_query`** (`services/catalog/search/normalize.py:18`). The `re` module caches compiled patterns by pattern string, so a repeat call costs a dict lookup of about 0.5–1 µs, not a full compile. At 300 req/s that is 0.15–0.3 ms of CPU time per second, under 0.01% of the 30 ms search p50. Move the pattern beside `_WHITESPACE` at `services/catalog/search/normalize.py:10` for consistency, not for speed.
- **Dismissed — the inventory batch reservation path** (`services/inventory/reservations/batch.py:47-77`). The code already passes the whole SKU set as one array parameter to each statement. One call handles up to 100 SKUs in 4 round trips inside one transaction. The lock statement orders by SKU to keep the lock graph acyclic (`services/inventory/reservations/batch.py:25`). No change is needed.
- **Dismissed — the search price hydration path** (`services/catalog/search/hydrate.py:31-46` and `services/catalog/clients/pricing_client.py:19`). `attach_prices` already fetches the whole page in one batch POST and indexes the reply by product id. This is the correct shape, and it stands in contrast to `attach_sellers` in the same file.
- **Dismissed — the event publisher** (`libs/eventkit/publisher.py:32-50`). `flush` already pipelines up to `PUBLISH_FLUSH_COUNT` (200) events into one round trip. `libs/eventkit/codec.py:27-33` also builds one encoder and one decoder at import and shares them.
- **Dismissed — the bin allocator** (`services/inventory/allocator.py:26-45`). `bin_weight` carries `functools.lru_cache(maxsize=8192)`, so a repeat SKU costs one cache lookup. The fold loop is a fixed 8 iterations, and the comment at `services/inventory/allocator.py:41` explains why it exists. The `hashlib.sha1` call costs about 1 µs and runs once per new SKU, at 33 reservations per second.
- **Dismissed — the dbkit metrics lock** (`libs/dbkit/metrics.py:17-30`). A mutex costs about 15 ns and each helper takes it once per call. No serving-path caller invokes `incr` or `observe` inside a loop, so there is no per-item lock to hoist.
- **Dismissed — the promoted-list and category-tree reloads** (`services/catalog/search/promoted.py:28` and `services/catalog/categories.py:41`). Both guard the query behind a timestamp check, so the reload runs at most once per hour and every request in between reads process memory. This is the correct shape.
- **Dismissed — the pricing dedupe window** (`services/pricing/consumer/dedupe.py:16-27`). `seen_before` uses an `OrderedDict` with `move_to_end` and `popitem`, so each event costs O(1) and the window stays bounded at `DEDUPE_CAPACITY` (8,192).