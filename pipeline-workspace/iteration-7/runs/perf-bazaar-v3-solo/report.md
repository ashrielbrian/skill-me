I have read the code I need. Here is the audit.

# Performance Audit Report

## Summary

`bazaar` is a Python marketplace monorepo. Six services share three libraries, one Postgres cluster, and Redis streams. The hot paths are the gateway edge (1,200 req/s), catalog search (300 req/s) and detail (500 req/s), checkout order placement (13 orders/s over 8 line items), and the pricing consumer (200 events/s with a 5 ms per-event budget). The single best quick win is one word in `services/pricing/campaigns.py:20`: return a `set` instead of a `list`, and the campaign match drops from about 1.5 ms per event to about 1 µs. The largest win overall is a cache for the 500 KB promo snapshot that `services/pricing/consumer/rules.py:13` reads once for each event.

## Findings

### [P1] The pricing consumer reads and parses the 500 KB promo snapshot for each event
- **Category**: I/O and Round Trips
- **Location**: `services/pricing/consumer/rules.py:8-16`, called from `services/pricing/consumer/apply.py:22`
- **Hot path**: `services/pricing/consumer/loop.py:54` awaits `apply_price_update(event)` for each entry of every batch. The loop reads batches of `STREAM_READ_COUNT` = 100 entries (`services/pricing/config.py:17`) and the service consumes 12,000 events/min = 200 events/s.
- **Problem**: `load_active_promos` runs one Redis GET and one `json.loads` for each event. The snapshot is about 500 KB. The README states that the admin CLI publishes the snapshot hourly, so the data changes 1 time per hour but the worker reads it 720,000 times per hour.
- **Evidence**:
```python
async def load_active_promos(redis_client):
    """Returns the active promo ruleset keyed by SKU.

    The snapshot is about 500 KB. Called before each event is applied.
    """
    raw = await redis_client.get(PROMO_SNAPSHOT_KEY)
```
- **Estimate**: This is an estimate. Assume `json.loads` decodes a dict-heavy payload at 100 MB/s. 500 KB / 100 MB/s = 5 ms. The client sets `decode_responses=True` (`services/pricing/consumer/apply.py:17`), which adds a UTF-8 decode of 500 KB at about 1 GB/s = 0.5 ms. The Redis round trip plus the transfer of 500 KB adds about 1 ms. The total is 6.5 ms to 10 ms for each event, against a documented budget of 5 ms for each event. At 200 events/s the worker spends 1.3 s to 2.0 s of CPU time per second on this one call, and it reads 100 MB/s = 800 Mbit/s from Redis. A 60-second cache cuts 12,000 reads per minute to 1 read per minute, a reduction of 12,000 times, and returns the whole 5 ms budget.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Cache the parsed dict in the worker process with a short TTL, in the same shape as `services/catalog/search/promoted.py:18-33`. Measure the p99 in the batch log line at `services/pricing/consumer/loop.py:64` before and after the change. Also measure Redis network bytes out.

### [P1] Search rank tests membership against a list of 3,000 promoted ids
- **Category**: Algorithms
- **Location**: `services/catalog/search/rank.py:26`, list built at `services/catalog/search/promoted.py:14` and `services/catalog/search/promoted.py:31`
- **Hot path**: `services/catalog/routes.py:48` calls `rank.rank_page(candidates, promoted_ids, tenant=tenant)` for each search request. `rank_page` calls `rerank`, which loops over `RANK_CANDIDATES` = 500 rows (`services/catalog/config.py:13`). Search runs about 300 times per second.
- **Problem**: `promoted_ids` is a `list` of about 3,000 ints. The test `candidate["product_id"] in promoted_ids` scans the list. Most candidates are not promoted, so most tests scan all 3,000 entries. The work is 500 × 3,000 = 1,500,000 integer comparisons for each search request.
- **Evidence**:
```python
def rerank(candidates: list[dict], promoted_ids: list[int]) -> list[dict]:
    for candidate in candidates:
        ...
        if candidate["product_id"] in promoted_ids:
            score += _PROMOTED_BOOST
```
- **Estimate**: This is an estimate. Assume 10 ns to 15 ns for one element of a CPython list scan over ints. One full scan costs 3,000 × 10 ns = 30 µs to 45 µs. 500 candidates cost 15 ms to 22 ms for each search request. The documented search p50 is 30 ms, so this step is half of the budget or more. At 300 req/s the service spends 4.5 s to 6.6 s of CPU time per second here, which needs 5 to 7 cores. A `frozenset` makes each test one hash probe of about 40 ns, so 500 probes cost 20 µs. The saving is about 15 ms for each search request, a reduction of about 750 times on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Store the promoted ids as a `frozenset` in `promoted.py` and keep the hourly reload. Build the set once per reload, not once per request. Change the type hints in `rank.rerank` and `rank.rank_page`. Keep `loaded_count()` working, because `len()` works on a set. Measure search p50 and p99 before and after the change.

### [P1] Search hydration runs one seller query for each page row
- **Category**: I/O and Round Trips
- **Location**: `services/catalog/search/hydrate.py:16-20`
- **Hot path**: `services/catalog/routes.py:49` awaits `hydrate.attach_sellers(page)` for each search request. The page holds `SEARCH_PAGE_SIZE` = 25 rows (`services/catalog/config.py:12`). Search runs about 300 times per second.
- **Problem**: The loop awaits one `fetch_one` for each row, so the 25 queries run in sequence. Each `fetch_one` also acquires and releases a pooled connection (`libs/dbkit/executor.py:29-31`). The pool holds `POOL_MAX` = 20 connections for each process (`libs/dbkit/config.py:13`).
- **Evidence**:
```python
    for row in rows:
        seller = await fetch_one(
            "SELECT display_name, badge FROM sellers WHERE id = $1",
            (row["seller_id"],),
        )
```
- **Estimate**: This is an estimate. The README gives 0.5 ms to 2 ms for a simple indexed query round trip. 25 sequential queries cost 12.5 ms to 50 ms for each search request, against a documented p50 of 30 ms. At 300 req/s the service sends 7,500 seller queries per second and holds about 7.5 connection-seconds per second out of 20 connections. One query with `WHERE id = ANY($1)` costs 1 round trip of 0.5 ms to 2 ms. The saving is 12 ms to 48 ms for each request, and the query rate falls from 7,500/s to 300/s, a reduction of 25 times.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Collect the distinct seller ids, run one `WHERE id = ANY($1)` query, and index the result by id. `attach_prices` in the same file (`services/catalog/search/hydrate.py:35-48`) already shows this shape: one bulk call plus `_price_map`. Measure search p50 and the Postgres query rate for the `sellers` table.

### [P1] The gateway reads and parses the rate rules file for each request
- **Category**: I/O and Round Trips
- **Location**: `services/gateway/middleware/ratelimit.py:15-22`
- **Hot path**: `services/gateway/middleware/ratelimit.py:56` calls `check(tenant_id)` inside the rate-limit middleware. `services/gateway/app.py:26-31` installs that middleware in the chain, so it runs for every request. The edge takes 1,200 req/s at peak with a p50 of 8 ms.
- **Problem**: `check` opens `config/rate_rules.json` and parses it for each request. The README states the file holds about 2,000 tenant entries. The `open` and the `read` are synchronous, so they block the asyncio event loop and stall every other request in flight. The docstring states the intent: "Runs for each request."
- **Evidence**:
```python
def check(tenant_id):
    """Returns the per-minute budget for a tenant. Runs for each request."""
    with open(RATE_RULES_PATH) as f:
        rules = json.load(f)
    rule = rules.get(tenant_id)
```
- **Estimate**: This is an estimate. Assume each entry is a key plus a one-field object, about 45 bytes, so the file is about 90 KB. The parse creates about 8,000 objects (2,000 keys, 2,000 inner dicts, 2,000 inner keys, 2,000 ints) at 100 ns to 150 ns each, which is 0.8 ms to 1.2 ms. The `open` syscall and the read from the page cache add about 20 µs. The total is about 1 ms for each request, or 12% of the 8 ms p50. At 1,200 req/s the process needs 1.2 s of blocking CPU time per second, which one event loop cannot supply. The parse also churns about 200 KB per request, or 240 MB/s. A module-level load cuts the cost to one dict lookup of about 50 ns, a reduction of about 20,000 times.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Load the rules file once at import time, as `services/gateway/config.py:1-4` already documents for the other settings ("Values load once at import time; the process restarts to pick up changes"). If operators need reloads without a restart, add a modification-time check with a minimum interval of several seconds, and do the check off the event loop. Measure gateway p50 and p99, and measure event-loop lag.
- **Note**: `services/gateway/tests/test_ratelimit.py:12` patches `builtins.open` around `check`, so the fix must also update that test.

### [P1] Order placement calls the inventory service once for each line item
- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/reserve_step.py:22-34`
- **Hot path**: `services/checkout/orders/create.py:47` awaits `reserve_items(order.id, priced)` for each placed order. Orders average 8 line items and the service places about 800 orders/min = 13 orders/s.
- **Problem**: The loop awaits one HTTP POST to `/v1/reserve` for each line item, so 8 sibling-service calls run in sequence. The inventory service already exposes a batch endpoint for up to 100 items: `services/inventory/routes.py:46-53` registers `POST /v1/reserve/batch`, and `services/inventory/reservations/batch.py:47-91` reserves the whole batch with a fixed number of statements and array parameters. Checkout does not use it.
- **Evidence**:
```python
    for item in items:
        response = await _client.post(
            INVENTORY_BASE_URL + "/v1/reserve",
            json={...},
        )
```
- **Estimate**: This is an estimate. The README gives 5 ms to 20 ms for a sibling-service HTTP call. 8 sequential calls cost 40 ms to 160 ms for each order, against a documented p50 budget of 90 ms. One batch call costs 5 ms to 20 ms. The saving is 35 ms to 140 ms for each order, a reduction of 8 times in call count. The inventory service also drops from 104 reserve calls per second to 13, and each order then holds one transaction instead of 8.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Call `POST /v1/reserve/batch` with all line items in one request. The batch path is atomic, so it also removes the need for the compensating `_release_all` call at `services/checkout/orders/reserve_step.py:32`. Note that the batch payload needs a `zone` field (`services/inventory/models.py:91-104`), which the single-item path also needs. Measure order p50 and the inventory request rate.

### [P1] `fetch_all` serializes every result set for a debug log that is switched off
- **Category**: Observability Overhead
- **Location**: `libs/dbkit/executor.py:23`
- **Hot path**: Every service reads Postgres through `libs/dbkit`. The largest caller is `services/catalog/search/query.py:69`, which fetches `RANK_CANDIDATES` = 500 rows for each of about 300 searches per second. The second largest is `services/settlement/cron.py:43`, which fetches about 1.2M order rows once per night.
- **Problem**: The code builds the whole JSON string before the call, so `json.dumps` runs even when the DEBUG level is off. All three service entry points set the level to INFO: `services/gateway/app.py:41`, `services/catalog/app.py:41`, and `services/pricing/worker.py:40`. `services/checkout/config.py:30` defaults `LOG_LEVEL` to `INFO`. The logger therefore discards 100% of this work.
- **Evidence**:
```python
    rows = [dict(record) for record in records]
    logger.debug("rows=%s", json.dumps(rows, default=str))
    return rows
```
- **Estimate**: This is an estimate. Assume the C JSON encoder runs at 100 MB/s to 200 MB/s. A 500-row search result with 9 fields per row is about 100 KB of JSON, so the encode costs 0.5 ms to 1 ms for each search. At 300 req/s that is 150 ms to 300 ms of CPU time per second, and 3% of the 30 ms p50. The settlement case is worse: 1.2M rows at about 150 bytes of JSON each is a transient string of about 180 MB, which costs about 1.8 s of CPU time and raises the peak memory of the nightly run. One level check removes all of it.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Guard the call with `if logger.isEnabledFor(logging.DEBUG):` before the `json.dumps`. Do the same for the row count if operators want a cheap always-on line. Measure catalog search p50 and the peak resident memory of the settlement run.

### [P1] Campaign match tests membership against a list of 2,000 SKUs
- **Category**: Algorithms
- **Location**: `services/pricing/consumer/match.py:10-12`, list built at `services/pricing/campaigns.py:20`
- **Hot path**: `services/pricing/consumer/loop.py:53` calls `find_affected_skus(event, campaign_skus)` for each event. `services/pricing/consumer/loop.py:34` loads the roster once at worker start. The service consumes 200 events/s.
- **Problem**: `load_campaign_skus` returns a `list` of about 2,000 SKU strings. `find_affected_skus` runs `sku in campaign_skus` for each candidate SKU on the event, and the docstring states that events carry up to about 30 candidate SKUs. The work is up to 30 × 2,000 = 60,000 string comparisons for each event.
- **Evidence**:
```python
async def load_campaign_skus():
    """Returns the SKUs currently on campaign. Called once at startup."""
    rows = await fetch_all(ROSTER_QUERY)
    return [row["sku"] for row in rows]
```
- **Estimate**: This is an estimate. Assume 25 ns for one element of a CPython list scan over short strings that differ early. One full scan costs 2,000 × 25 ns = 50 µs. 30 candidates cost 1.5 ms for each event, which is 30% of the documented 5 ms per-event budget. At 200 events/s that is 300 ms of CPU time per second, or 30% of a core for each worker. 30 set probes cost 30 × 40 ns = 1.2 µs. The saving is about 1.5 ms for each event, a reduction of about 1,250 times on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change `services/pricing/campaigns.py:20` to return a set: `return {row["sku"] for row in rows}`. `roster_digest` at `services/pricing/campaigns.py:43-47` reads `skus[0]`, so update that function too. The caller only needs the count of matches (`services/pricing/consumer/loop.py:53`), so `find_affected_skus` can also return a count instead of a list. Measure the p99 in the batch log line.

### [P1] Session attachment makes a blocking HTTP call on the event loop
- **Category**: I/O and Round Trips
- **Location**: `services/gateway/handlers/session.py:28-31`
- **Hot path**: `services/gateway/middleware/auth.py:41` and `services/gateway/middleware/auth.py:44` await `attach_session(request)`. The README states that about 70% of the 1,200 req/s carry a session token, so about 840 requests per second reach this code. The call runs only on a cache miss (`services/gateway/handlers/session.py:23`).
- **Problem**: The function uses `urllib.request.urlopen`, which is synchronous. Inside a coroutine it blocks the whole event loop for the full duration of the auth call, so every other request in the process stalls. The package already holds a pooled async client for the same service: `services/gateway/clients/auth_client.py:10` builds one `httpx.AsyncClient`, and `logout` at `services/gateway/handlers/session.py:65` uses it.
- **Evidence**:
```python
    payload = json.dumps({"token": token}).encode("utf-8")
    raw = urllib.request.urlopen(
        AUTH_BASE_URL + "/v1/verify", data=payload, timeout=REQUEST_TIMEOUT_S
    )
```
- **Estimate**: This is an estimate. The README gives 5 ms to 20 ms for a sibling-service HTTP call. `urlopen` also opens a new TCP connection for each call, which adds one round trip of about 50 µs inside the datacenter. Assume a cache TTL of 300 s (`services/gateway/config.py:20`) and a miss rate of 5%, so about 42 misses per second. Each miss stalls the loop for 5 ms to 20 ms, which is 210 ms to 840 ms of stall time per second. Every other request pays that delay as queue time on top of its own 8 ms p50. The pooled async client removes the stall completely and reuses the connection. The `REQUEST_TIMEOUT_S` = 10 setting means one slow auth call can stall the process for 10 s.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a `verify(token)` function to `services/gateway/clients/auth_client.py` and await it from `attach_session`. Measure event-loop lag and gateway p99.

### [P1] Webhook delivery runs inline and in sequence on the order path
- **Category**: I/O and Round Trips
- **Location**: `services/checkout/webhooks/notify.py:26-50` and `services/checkout/webhooks/notify.py:19`
- **Hot path**: `services/checkout/orders/create.py:52` awaits `dispatch("order.placed", ...)` before `place_order` returns its response. The service places about 13 orders per second. `services/checkout/orders/create.py:71` awaits the same function on cancel.
- **Problem**: There are two defects in one function. First, the loop awaits each delivery in sequence, so a slow subscriber delays every later subscriber and the client response. The per-subscriber timeout is `WEBHOOK_TIMEOUT_S` = 5 (`services/checkout/config.py:9`), so S subscribers can add up to S × 5 s to one order response. The docstring claims that "one bad endpoint cannot block the rest", which holds for errors but not for delay. Second, `send_order_event` builds a new `httpx.AsyncClient` for each delivery, so each delivery pays a fresh TCP handshake and a fresh TLS handshake. `services/checkout/webhooks/registry.py:39` requires `https` for every subscription URL.
- **Evidence**:
```python
async def send_order_event(url, event):
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
        response = await client.post(url, json=event)
...
    for url in urls:
        try:
            await send_order_event(url, event)
```
- **Estimate**: This is an estimate. Subscriber endpoints are customer-owned, so assume an internet round trip of 150 ms. A new TLS connection costs 3 round trips (TCP plus TLS) = 450 ms, against about 150 ms on a reused keep-alive connection. With 3 subscribers, the sequential loop adds 3 × 600 ms = 1.8 s to an order whose p50 budget is 90 ms, which is 20 times the budget. `asyncio.gather` cuts the wall time to the slowest single delivery, about 600 ms. A shared client removes the two handshake round trips, so 600 ms falls to about 150 ms. Together the saving is about 1.65 s for each order, a reduction of about 12 times. A move of the dispatch off the request path removes all of it from the order response.
- **Impact**: High
- **Effort**: Medium (hours)
- **Suggested fix**: Take three steps. First, move the client to module level, as `services/checkout/clients/inventory_client.py:12` already does. Second, replace the loop with `asyncio.gather(..., return_exceptions=True)`. Third, move the dispatch off the request path onto a background task or a queue, so a customer endpoint can never delay an order response. `libs/eventkit/publisher.py` already offers a buffered producer for that shape. Measure order p50 and p99, and count how many orders wait on a webhook.

### [P2] Order pricing runs one price query for each line item
- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/pricing_step.py:23-32`
- **Hot path**: `services/checkout/orders/create.py:43` awaits `price_items(items, request["region"])` for each placed order. Orders average 8 line items and the service places 13 orders per second.
- **Problem**: The loop awaits one `fetch_one` for each item, so 8 queries run in sequence. Each query also acquires a pooled connection.
- **Evidence**:
```python
    for item in items:
        row = await fetch_one(_PRICE_QUERY, (item.product_id,))
```
- **Estimate**: This is an estimate. At 0.5 ms to 2 ms for each indexed query, 8 sequential queries cost 4 ms to 16 ms for each order, which is 4% to 18% of the 90 ms p50 budget. One `WHERE product_id = ANY($1)` query costs 0.5 ms to 2 ms. The saving is 3.5 ms to 14 ms for each order, and the query rate falls from 104/s to 13/s, a reduction of 8 times.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Fetch all prices in one `ANY($1)` query, index the rows by `product_id`, then loop in memory. Raise `PricingFailure` for the first product id that the result set does not cover, which keeps the current error behaviour. Measure order p50 and the query rate for the `prices` table.

### [P2] Tax lookup scans 5,100 rules for each call
- **Category**: Algorithms
- **Location**: `libs/commerce/tax.py:29-32`, table built at `libs/commerce/tax.py:16-24`
- **Hot path**: Two callers. `services/pricing/consumer/apply.py:32` calls `rate_for(event.region, event.category)` once for each of 200 events per second. `services/checkout/orders/pricing_step.py:29` calls it once for each line item, so 8 times for each of 13 orders per second.
- **Problem**: `_RULES` is a list of tuples, one for each pair of region and category. `libs/commerce/regions.py:8-24` lists 150 regions and `libs/commerce/catalog_types.py:8-16` lists 34 categories, so the table holds 150 × 34 = 5,100 tuples, which matches the README figure of about 5,000 rows. `rate_for` walks the list and compares two strings for each row. The loop puts the region first, so a region late in the list needs a near-complete scan. A dict keyed by the pair gives the same answer in one probe.
- **Evidence**:
```python
def rate_for(region, category) -> float:
    """Return the tax rate for a region and product category."""
    for rule_region, rule_category, rate in _RULES:
        if rule_region == region and rule_category == category:
            return rate
    return DEFAULT_RATE
```
- **Estimate**: This is an estimate. Assume 120 ns for one row, which covers the tuple unpack and the two string comparisons. A match halfway through the table costs 2,550 × 120 ns = 306 µs. A miss costs the full 5,100 × 120 ns = 612 µs. Two facts push the pricing consumer toward the miss cost: `services/pricing/consumer/decode.py:15` defaults `region` to lower-case `"us"`, and `REGIONS` holds upper-case `"US"`, so no row ever matches and every call scans all 5,100 rows. That is 612 µs for each event, or 12% of the 5 ms budget, and 122 ms of CPU time per second at 200 events/s. Checkout pays 8 × 306 µs = 2.4 ms for each order. A dict probe costs about 60 ns, so the saving is about 600 µs for each event and about 2.4 ms for each order, a reduction of about 5,000 times on this step.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build `_RULES` as a dict keyed by the `(region, category)` tuple and look the rate up with one `get` call. Keep `rule_count()` working, because `len()` works on a dict. Also check the region case at `services/pricing/consumer/decode.py:15`, because the current default never matches a rule and every pricing event silently takes `DEFAULT_RATE` = 0.05. Measure the p99 in the batch log line.

### [P2] `convert` parses the currency rate table on each call
- **Category**: Redundant Work
- **Location**: `libs/commerce/money.py:53`, table declared at `libs/commerce/money.py:17-48`
- **Hot path**: `services/pricing/consumer/apply.py:25`, `services/pricing/consumer/apply.py:29`, and `services/pricing/consumer/apply.py:31` call `convert` two or three times for each of 200 events per second. `services/checkout/orders/pricing_step.py:28` calls it once for each line item, so 8 times for each of 13 orders per second.
- **Problem**: The rate table is a JSON string constant, and `convert` calls `json.loads` on it for every call. The table holds 150 currencies, which matches the README figure. The comment at `libs/commerce/money.py:64-69` states that the display quantum constants "are built once here; every format call shares them", so the module already applies the correct pattern to its other constants.
- **Evidence**:
```python
def convert(amount_cents, from_ccy, to_ccy) -> int:
    """Convert an integer minor-unit amount between currencies."""
    rates = json.loads(_RATES_JSON)
```
- **Estimate**: This is an estimate. The literal is about 2.6 KB. The parse creates about 450 objects (150 keys, 150 float values, 150 dict inserts) at 100 ns to 200 ns each, which is 30 µs to 60 µs, plus a 150-entry dict of about 9 KB. The pricing consumer pays 3 × 30 µs = 90 µs for each event, or 1.8% of the 5 ms budget, and it churns 27 KB per event = 5.4 MB/s. Checkout pays 8 × 30 µs = 240 µs for each order. Across both callers the platform runs about 700 parses per second, which is 21 ms of CPU time per second and 6.3 MB/s of garbage. A module-level dict cuts the cost to two dict probes of about 100 ns, a reduction of about 300 times.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `_RATES = json.loads(_RATES_JSON)` at module level and read `_RATES` in `convert`. `libs/eventkit/codec.py:26-31` shows the same pattern for the encoder and decoder. Measure the pricing p99 and the checkout order p50.

### [P2] The latency window sorts 2,048 samples for each event
- **Category**: Observability Overhead
- **Location**: `services/pricing/consumer/stats.py:15-20`
- **Hot path**: `services/pricing/consumer/loop.py:56` calls `tracker.record(...)` for each event, so 200 times per second. The value is read once for each batch of 100 events, at `services/pricing/consumer/loop.py:64`.
- **Problem**: `record` sorts the whole window on every call to refresh `p99_ms`. The window holds `STATS_WINDOW` = 2,048 samples (`services/pricing/config.py:23`). The reader needs the p99 one time for each batch, so the code does 100 sorts where 1 sort answers the same question.
- **Evidence**:
```python
    def record(self, value):
        """Adds one sample and refreshes the p99. Called once per event."""
        self._window.append(value)
        ordered = sorted(self._window)
        rank = max(0, int(len(ordered) * 0.99) - 1)
        self.p99_ms = ordered[rank]
```
- **Estimate**: This is an estimate. A sort of 2,048 floats needs about 2,048 × log2(2,048) = 22,500 comparisons. Assume 18 ns for each comparison on the float fast path, which is 405 µs, plus about 25 µs to copy the deque into a list. The total is about 430 µs for each event, or 8.6% of the 5 ms budget, and 86 ms of CPU time per second at 200 events/s. Each sort also allocates a list of 2,048 pointers, which is 16 KB per event = 3.2 MB/s of garbage. A move of the sort into `summary()` cuts 100 sorts per batch to 1, a reduction of 99%, and saves about 425 µs for each event.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Delete the sort from `record` and compute the p99 inside `summary()`, which the batch log line calls once. Keep the `p99_ms` attribute if other code reads it. Measure the p99 line itself, and compare the batch wall time before and after the change.

### [P2] `apply_price_update` wraps one UPDATE in an explicit transaction
- **Category**: I/O and Round Trips
- **Location**: `services/pricing/consumer/apply.py:34-46`
- **Hot path**: `services/pricing/consumer/loop.py:54` awaits `apply_price_update(event)` for each of 200 events per second.
- **Problem**: The `async with conn.transaction()` block holds one statement. asyncpg sends `BEGIN` and `COMMIT` as separate round trips, so one UPDATE becomes 3 round trips. Postgres commits a lone statement on its own, so the extra 2 round trips buy no extra atomicity. The multi-statement paths in `services/inventory/reservations/batch.py:57` and `services/checkout/orders/txn.py:45` need the explicit transaction; this single-statement path does not.
- **Evidence**:
```python
    async with (await get_pool()).acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE prices SET base_cents = $1, promo_cents = $2, ...
```
- **Estimate**: This is an estimate. The README gives 0.5 ms to 2 ms for a simple query round trip; `BEGIN` and `COMMIT` are cheaper because they run no query plan, so assume 0.3 ms to 0.5 ms each. The two extra round trips cost 0.6 ms to 1.0 ms for each event, which is 12% to 20% of the 5 ms budget. At 200 events/s the cluster handles 400 extra round trips per second. A commit also forces a write-ahead-log flush of 1 ms to 10 ms, and one commit for each event caps a single worker at a few hundred events per second.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Drop the `conn.transaction()` block and let the single UPDATE autocommit, or move the whole batch of 100 events into one transaction beside the history flush at `services/pricing/consumer/loop.py:58`. The second option also cuts 100 commits per batch to 1. Measure the Postgres transaction rate and the batch wall time.

### [P2] Product detail reads every column, including the 20 KB description
- **Category**: Data Layout
- **Location**: `services/catalog/products/repo.py:16-19`
- **Hot path**: `services/catalog/routes.py:67` awaits `repo.get_product(product_id)` for each detail request that misses the Redis cache. Detail runs about 500 times per second and the cache TTL is `DETAIL_CACHE_TTL_S` = 60 (`services/catalog/config.py:16`).
- **Problem**: The query is `SELECT *`, but `views.detail_view` reads exactly 8 fields (`services/catalog/products/views.py:19-30`). The README states that a typical `description_html` column holds about 20 KB, so each uncached request transfers and decodes about 20 KB that the response never uses. The same file names the 8 columns it needs in `_LISTING_COLUMNS` (`services/catalog/products/repo.py:22-25`) for the other two queries, so the correct column list already exists.
- **Evidence**:
```python
    return await fetch_one(
        "SELECT * FROM products WHERE id = $1",
        (product_id,),
    )
```
- **Estimate**: This is an estimate. A 20 KB column at that size lives in TOAST storage, so Postgres reads 20 KB / 8 KB = 3 extra pages. From SSD that is 3 × 20 µs = 60 µs, plus decompression. The transfer of 20 KB over a 10 Gbit link costs about 16 µs, and the UTF-8 decode into a Python string costs about 20 µs at 1 GB/s. The total extra cost is about 100 µs for each uncached request. Assume a 50% cache miss rate, so 250 uncached requests per second: that is 25 ms of CPU time per second and 5 MB/s of extra network traffic and garbage. A named column list removes all of it.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace `SELECT *` with the 8 columns that `detail_view` reads. Note that `services/catalog/routes.py:72` also reads `row["category"]`, so keep `category` in the list. Measure the bytes that Postgres sends and the detail p50.

### [P2] `audit_context` walks the call stack twice for each audit record
- **Category**: Redundant Work
- **Location**: `libs/commerce/audit.py:26-40` and `libs/commerce/audit.py:48`
- **Hot path**: `services/checkout/orders/create.py:50` calls `write_audit(audit_context())` once for each of 13 orders per second. `services/settlement/payouts/ledger.py:48` calls the same pair once for each ledger batch of `LEDGER_BATCH_SIZE` = 1,000 rows (`services/settlement/config.py:15`).
- **Problem**: The caller builds the context, and then `write_audit` builds it a second time at line 48 and overwrites it with `entry.update(record)`. So `traceback.extract_stack()` runs twice for each record. `extract_stack` walks every frame, builds one `FrameSummary` for each frame, and calls `linecache.checkcache` for each unique source file, which runs one `os.stat` syscall per file. On the asyncio and aiohttp request path the stack spans tens of frames and 10 to 20 unique files.
- **Evidence**:
```python
def audit_context() -> dict:
    """Build the shared context fields for one audit record."""
    frames = traceback.extract_stack()
...
async def write_audit(record):
    ...
    entry = audit_context()
    entry.update(record)
```
- **Estimate**: This is an estimate. Assume 40 frames at 5 µs each for the walk and the `FrameSummary` objects, which is 200 µs, plus 15 `os.stat` calls at 1.5 µs each, which is 22 µs. One call costs about 220 µs and two calls cost about 440 µs, or 0.5% of the 90 ms order budget. The syscalls run on the event loop and block it. At 13 orders/s the service spends about 6 ms of CPU time per second and makes about 390 `os.stat` calls per second. Removal of the duplicate call halves the cost at once. Replacement of `extract_stack` with a `sys._getframe` walk that skips the line lookup removes the `os.stat` calls and cuts the rest by about 10 times.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Take two steps. First, delete the second `audit_context()` call inside `write_audit` and accept the caller's context. Second, replace `traceback.extract_stack()` with a `sys._getframe` walk, which needs no source lines and no `os.stat` calls. Keep the `caller` field format unchanged, because the settlement export and the dashboards read it. Measure order p50 and count `os.stat` calls with `strace` or `dtruss`.
- **Note**: `services/gateway/handlers/orders_proxy.py:27-28` calls `audit_context(session.tenant_id)` as an async context manager with `write_audit(ctx, actor_id=..., action=...)`. That call does not match the library signature, so fix the call site at the same time.

### [P3] Order context runs three independent queries in sequence
- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/context.py:20-22`
- **Hot path**: `services/checkout/orders/create.py:40` awaits `load_order_context(...)` for each of 13 orders per second.
- **Problem**: The three reads take a buyer id and a cart id that the caller already holds, so none of them depends on another. The sequential awaits cost 3 round trips where 1 does the work.
- **Evidence**:
```python
    buyer = await fetch_one(_BUYER_QUERY, (buyer_id,))
    cart = await fetch_one(_CART_QUERY, (cart_id,))
    address = await fetch_one(_ADDRESS_QUERY, (buyer_id,))
```
- **Estimate**: This is an estimate. At 0.5 ms to 2 ms for each query, the three sequential reads cost 1.5 ms to 6 ms. `asyncio.gather` cuts the wall time to the slowest single read, 0.5 ms to 2 ms. The saving is 1 ms to 4 ms for each order, or 1% to 4% of the 90 ms budget.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the three reads in `asyncio.gather`. Keep the current order of the validity checks so the client still sees the same first error. Measure order p50.

### [P3] The detail handler runs three queries in sequence
- **Category**: I/O and Round Trips
- **Location**: `services/catalog/routes.py:67-72`
- **Hot path**: The detail handler serves about 500 requests per second, and it reaches these lines when the Redis cache misses.
- **Problem**: `reviews.review_summary(product_id)` needs only the product id, so it does not depend on `repo.get_product`. `related.related_products` does need `row["category"]`. So the handler can run the product read and the review read together, which cuts 3 sequential round trips to 2.
- **Evidence**:
```python
    row = await repo.get_product(product_id)
    ...
    payload["reviews"] = await reviews.review_summary(product_id)
    strip = await related.related_products(product_id, row["category"])
```
- **Estimate**: This is an estimate. At 0.5 ms to 2 ms for each query, 3 sequential reads cost 1.5 ms to 6 ms and 2 stages cost 1 ms to 4 ms. The saving is 0.5 ms to 2 ms for each uncached request. At 500 req/s with an assumed 50% miss rate, that is 125 ms to 500 ms of latency saved per second.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Gather the product read and the review read, then run the related read after the product row arrives. Measure detail p50 and the cache miss rate.

### [P3] `candidate_rows` copies all 500 candidate rows a second time
- **Category**: Allocation and Copies
- **Location**: `services/catalog/search/query.py:74`, first copy at `libs/dbkit/executor.py:22`
- **Hot path**: `services/catalog/routes.py:46` awaits `query.candidate_rows(terms, category=category)` for each of about 300 searches per second. The row count is `RANK_CANDIDATES` = 500.
- **Problem**: `fetch_all` already converts each asyncpg record into a dict, and its docstring states so: "Rows come back as plain dicts". `candidate_rows` then runs `dict(row)` over the same rows, which builds 500 more dicts and throws the first 500 away.
- **Evidence**:
```python
    return [dict(row) for row in rows]
```
- **Estimate**: This is an estimate. A copy of a 9-key dict costs about 300 ns, so 500 copies cost about 150 µs for each search, or 0.5% of the 30 ms p50. At 300 req/s that is 45 ms of CPU time per second and about 45 MB/s of extra garbage. Removal of the line saves all of it.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Return `rows` directly, because `fetch_all` already returns mutable dicts and `rank.rerank` writes `candidate["score"]` into them. `services/catalog/products/related.py:25` holds the same duplicate copy over 8 rows; fix it at the same time.

### [P3] `facet_counts` does three dict lookups for each row and field
- **Category**: Redundant Work
- **Location**: `services/catalog/search/facets.py:34-39`
- **Hot path**: `services/catalog/routes.py:53` calls `facets.facet_counts(candidates)` for each of about 300 searches per second, over 500 candidate rows.
- **Problem**: The inner loop runs `value not in counts[field]`, then `counts[field][value] = 0`, then a read and a write of the same key. That is 3 to 4 dict operations where 1 does the work. The loop also calls `_facet_value` for each row and field, which adds a Python function call. The work is 500 rows × 4 fields = 2,000 iterations for each request.
- **Evidence**:
```python
            value = _facet_value(row, field)
            if value not in counts[field]:
                counts[field][value] = 0
            counts[field][value] = counts[field][value] + 1
```
- **Estimate**: This is an estimate. Assume 400 ns for one iteration, which covers the function call and the four dict operations. 2,000 iterations cost about 800 µs for each search, or 2.7% of the 30 ms p50, and 240 ms of CPU time per second at 300 req/s. A `Counter` or a `defaultdict(int)` cuts the dict work by about half, which saves about 250 µs to 400 µs for each search.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the count body with `collections.Counter` per field, and inline the `rating_band` branch so the loop calls no Python function for the three plain fields. Measure search p50.

### [P3] The access log builds its JSON body for a debug level that is switched off
- **Category**: Observability Overhead
- **Location**: `services/gateway/middleware/accesslog.py:18-20` and `services/gateway/middleware/accesslog.py:30-40`
- **Hot path**: The access-log middleware sits first in the chain (`services/gateway/app.py:27`), so it runs for all 1,200 requests per second.
- **Problem**: The middleware builds a 7-key dict and `log_request` calls `json.dumps` on it before the log call. `services/gateway/app.py:41` sets the level to INFO, so the logger discards every DEBUG record and the serialization work is waste. The `uuid.uuid4().hex` call at line 26 is needed, because line 41 puts the value in a response header.
- **Evidence**:
```python
def log_request(context):
    """Emits one access record. Runs for each completed request."""
    logger.debug("request %s", json.dumps(context))
```
- **Estimate**: This is an estimate. `json.dumps` of a small 7-key dict costs 2 µs to 4 µs, and the dict build costs about 1 µs. The total is about 4 µs for each request, or 0.05% of the 8 ms p50, and about 5 ms of CPU time per second at 1,200 req/s. A level check removes it.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Check `logger.isEnabledFor(logging.DEBUG)` before the dict build. Note a second point for the operations team: the access log writes at DEBUG while the process runs at INFO, so the edge produces no access records at all today.

### [P3] The order status view reparses `items_json` to recompute a stored total
- **Category**: Redundant Work
- **Location**: `services/checkout/orders/summary.py:16-22`
- **Hot path**: Clients poll the order status endpoint about 40 times per second at peak, as `services/checkout/orders/summary.py:14` and the README both state.
- **Problem**: The query reads the whole `items_json` column, parses it, and sums the line values to produce `total_cents`. The `orders` table already stores that value: `services/checkout/orders/txn.py:7` lists `total_cents` in the insert column list, and `services/checkout/orders/create.py:44` computes it once at placement time. The view also needs only the item count from the parsed list.
- **Evidence**:
```python
    items = json.loads(row["items_json"])
    total_cents = sum(
        entry["unit_price_cents"] * entry["quantity"] for entry in items
    )
```
- **Estimate**: This is an estimate. An 8-item `items_json` value holds about 1.2 KB, so the transfer costs about 1 µs and `json.loads` costs about 12 µs. The sum over 8 entries costs about 2 µs. The total extra cost is about 15 µs for each poll, or about 1.5% of a 1 ms request, and 0.6 ms of CPU time per second at 40 req/s. A read of `total_cents` plus `jsonb_array_length(items_json)` in SQL removes the transfer and the parse.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Change the query to `SELECT status, total_cents, jsonb_array_length(items_json) AS item_count, updated_at`. Confirm the column type is `jsonb`; if it is `text`, keep the parse or add a stored item count. Compare the two totals in a test first, because a difference would show a data problem rather than a code problem.

### [P3] `render_report` runs the same aggregate query three times
- **Category**: Redundant Work
- **Location**: `services/settlement/reports/daily.py:19-21`
- **Hot path**: `services/settlement/cron.py:57` awaits `render_report(conn, day)` once for each nightly settlement pass over about 1.2M order rows.
- **Problem**: The three helper calls pass identical arguments, so the code runs the same `GROUP BY plan` aggregate three times and uses each result for a different part of the report.
- **Evidence**:
```python
    header = _header(await summarize_fees(conn, day), day)
    body = _body(await summarize_fees(conn, day))
    footer = _footer(await summarize_fees(conn, day))
```
- **Estimate**: This is an estimate. The aggregate scans one day of `payout_ledger`, which holds one row for each paying seller. Assume 50,000 rows and 50 ms to 500 ms for each scan and aggregate. The two extra runs waste 100 ms to 1 s once per night. One call and one reuse of the result removes all of it.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Call `summarize_fees` once, hold the rows in a local variable, and pass that variable to `_header`, `_body`, and `_footer`.

### [P3] `top_sellers` sorts 1.2M rows to return 50
- **Category**: Algorithms
- **Location**: `services/settlement/payouts/rollup.py:30-33`
- **Hot path**: `services/settlement/cron.py:58` calls `top_sellers(rows)` once for each nightly pass. `rows` is the full result of the orders query at `services/settlement/cron.py:43`, which the README sizes at about 1.2M order rows.
- **Problem**: A full sort orders 1.2M rows, and the caller then takes `TOP_SELLERS_LIMIT` = 50 (`services/settlement/config.py:12`). A heap of size 50 answers the same question in O(N log k).
- **Evidence**:
```python
def top_sellers(rows):
    """Returns the rows listed in the nightly summary table."""
    ordered = sorted(rows, key=lambda r: r["net_cents"], reverse=True)
    return ordered[:TOP_SELLERS_LIMIT]
```
- **Estimate**: This is an estimate. The sort calls the key function 1.2M times at about 150 ns each, which is 180 ms, and then runs about 1.2M × 20 = 24M comparisons at about 20 ns each, which is 480 ms. It also allocates a list of 1.2M pointers, or 9.6 MB. The total is about 0.7 s once per night. `heapq.nlargest(50, rows, key=...)` costs about 200 ms to 300 ms, so the saving is about 0.4 s per night.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Use `heapq.nlargest(TOP_SELLERS_LIMIT, rows, key=...)`. Also check the intent: the function sorts order rows, not the per-seller totals that `rollup_by_seller` returns, so the "seller" table at `services/settlement/cron.py:59-60` lists the 50 largest single orders. If the report needs the 50 largest sellers, sort the `totals` mapping instead, which holds one entry per seller rather than 1.2M rows. That change also makes the step about 20 times cheaper.

### [P3] The settlement pass loads 1.2M order rows into memory at once
- **Category**: Allocation and Copies
- **Location**: `services/settlement/cron.py:43`
- **Hot path**: `services/settlement/cron.py:80` calls `run_once()` once per day at 02:00 UTC over about 1.2M order rows.
- **Problem**: `fetch_all` materializes every row as a dict and returns one list. The pass then reads that list three times: `rollup_by_seller` at line 46, and `top_sellers` at line 58. asyncpg first builds 1.2M `Record` objects, and `libs/dbkit/executor.py:22` then builds 1.2M dicts, so both sets live at the same time at the peak.
- **Evidence**:
```python
    rows = await fetch_all(ORDERS_QUERY, day)
    LOG.info("settling %d order rows for %s", len(rows), day)
```
- **Estimate**: This is an estimate. A 6-key dict costs about 232 bytes, and its 6 value objects add about 200 bytes, so one row costs about 430 bytes. 1.2M rows cost about 520 MB. The transient `Record` set adds a similar amount, so the peak approaches 1 GB. The `json.dumps` call in the P1 finding above adds a further 180 MB string at that same moment. A server-side cursor holds one batch at a time and cuts the peak to a few megabytes.
- **Impact**: Medium
- **Effort**: Large (days)
- **Suggested fix**: Profile the resident memory of one nightly pass before you commit to this change, because the README states that a pass "finishes well inside the nightly window" today. If the peak is a risk, stream the rows through `conn.cursor(...)` as `services/inventory/reservations/gc.py:54` already does, and fold each row into the seller totals and into a 50-entry heap as it arrives. That change removes the need for a second pass over the rows and makes both the memory and the run time independent of the day's order count.

### [P4] The gateway parses and re-serializes every proxied response body
- **Category**: Redundant Work
- **Location**: `services/gateway/handlers/products_proxy.py:27`, `services/gateway/handlers/products_proxy.py:34`, `services/gateway/handlers/orders_proxy.py:29`, `services/gateway/handlers/orders_proxy.py:51`, and `services/gateway/responses.py:26`
- **Hot path**: The proxy handlers serve most of the 1,200 req/s at the edge. `services/gateway/handlers/orders_proxy.py:44` states that `get_order` alone runs about 40 times per second.
- **Problem**: Each handler calls `resp.json()` to parse the upstream body into Python objects, and `responses.envelope` then calls `json.dumps` to serialize the same data again. The gateway needs only to wrap the body in `{"status": ..., "data": ...}`, and it never reads the fields.
- **Evidence**:
```python
    payload = await catalog_client.search_products(_clean_params(request.query))
    return responses.envelope(payload)
```
- **Estimate**: This is an estimate. Assume a typical proxied body of 6 KB, which covers a 25-item search page. The parse costs about 60 µs and the re-serialization about 80 µs, so the round trip through Python objects costs about 140 µs for each request. That is 1.75% of the 8 ms p50, and about 170 ms of CPU time per second at 1,200 proxied requests per second. A byte-level splice of the upstream body removes both steps.
- **Impact**: Low
- **Effort**: Medium (hours)
- **Suggested fix**: Record this; do not schedule it. The saving of about 140 µs for each request does not repay the cost of a second response path, and a byte splice gives up the type checks that `resp.json()` performs today. Revisit it only if a profile shows JSON work at the top of the gateway CPU profile.

## Cold-Code Notes

- `services/catalog/search/semantic.py:32-44` — `semantic_rescore` is O(candidates × references × 64) with a pure-Python dot product. `services/catalog/config.py:14` sets `SEMANTIC_RESCORE = False` and nothing calls `register_embeddings`, so the code never runs. If the flag turns on with 50 reference vectors, the step costs 500 × 50 × 64 = 1.6M Python operations, or about 96 ms for each search on a 300 req/s path. Convert the vectors to `array` or `numpy` before you enable the beta.
- `services/gateway/handlers/export.py:23-24` — `export_activity` runs one `fetch_one` for each audit event, and `_EVENTS_SQL` carries no LIMIT. No route in `services/gateway/routes.py:14-27` references it, so it is unreachable today. A tenant with 50,000 events would need 50,000 sequential queries, or about 50 s. Batch the actor reads before you wire this handler to a route.
- `tools/backfill_sellers.py:36-47` — `match_legacy_rows` is an O(N×M) nested loop over two seller lists. It is a one-shot backfill for the completed 2.8 migration.
- `libs/commerce/skuid.py:22-32` — `normalize_sku` builds the `str.maketrans` table inside the function on every call, at a cost of about 1 µs to 2 µs. The only caller is `services/catalog/products/repo.py:32` (`get_by_sku`), which the admin CLI uses.
- `libs/commerce/flags.py:36` — `load_flag_file` calls `re.compile` inside the parse loop. The loop runs once at import over about 30 entries.
- `services/settlement/reports/render.py:10` — `format_money_rows` builds a new `decimal.Context` inside the loop. The loop runs once per night over 3 plan rows.
- `libs/dbkit/prepare.py:26-28` — `render_sql` runs one `re.sub` over the whole statement for each parameter, which is O(params × statement length). No service imports it.
- `libs/dbkit/batch.py:20-25` — `build_values_clause` grows a string with `+=` in a loop over up to `FLUSH_ROWS` = 500 rows. CPython's in-place string optimization usually keeps this linear. The callers are the nightly ledger write and the per-batch price history write.
- `tools/common/pg.py:16-39` — each helper calls `asyncio.run`, which destroys the event loop that the `libs/dbkit` pool's connections belong to. Each CLI statement therefore pays a fresh connection handshake of 5 ms to 50 ms. These scripts run a handful of statements and exit.
- `libs/dbkit/metrics.py` and `libs/eventkit/metrics.py` — both modules collect counters and timings, and no code imports either one. They cost nothing at run time. Delete them, or wire them to the metrics endpoints their docstrings describe.
- `libs/eventkit/publisher.py:41-50` — `flush` uses the synchronous `redis` client, so a call from an async service would block the event loop. No code imports the module today.

## Not Found

- **Data Layout**: No significant findings beyond the `SELECT *` case above. The reference tables use the right shapes: `libs/commerce/currencies.py:27-36` and `libs/commerce/regions.py:26-48` hold `frozenset` values for membership tests, `services/inventory/models.py:43-88` uses frozen slotted dataclasses, and `services/catalog/products/attributes.py:33` precomputes an order index. No nested map costs several lookups per access on a hot path.
- **Allocation and Copies**: No deep copies on any hot path. `grep` found no `copy.deepcopy` and no `JSON.parse(JSON.stringify(...))` equivalent. The HTTP clients are all process-wide and pooled — `services/gateway/clients/catalog_client.py:10`, `services/gateway/clients/auth_client.py:10`, `services/catalog/clients/pricing_client.py:12`, `services/checkout/clients/inventory_client.py:12`, and `services/settlement/banking.py:16` — with the one exception in the P1 webhook finding.
- **Algorithms, string concatenation**: `services/pricing/consumer/decode.py:18-22` builds a routing key with four `+=` operations. CPython's in-place optimization applies, and the cost is about 400 ns against a 5 ms per-event budget, so it is 0.008%. Dismissed. `services/catalog/search/highlight.py:45-54` and `services/settlement/reports/render.py:29` already use a parts list plus `join`.
- **Redundant Work, regex compilation**: `services/catalog/search/normalize.py:18` calls `re.compile(QUERY_PATTERN)` inside `normalize_query`. The call runs once per search request, not once per item, and the `re` module caches compiled patterns, so the cost is about 0.5 µs to 1 µs against a 30 ms p50, or 0.003%. Dismissed, although line 10 of the same file shows the module-level style. Every other pattern in the repository already compiles at import time: `services/inventory/models.py:16`, `services/checkout/orders/validation.py:13-14`, `services/checkout/promotions.py:8`, and `libs/commerce/skuid.py:13-14`.
- **Algorithms, sort for top-k on the search path**: `services/catalog/search/rank.py:30` sorts all 500 candidates and `head` returns 25. The sort costs about 500 key calls at 200 ns plus about 4,500 comparisons at 30 ns, or about 235 µs, which is 0.8% of the 30 ms p50. `heapq.nlargest(25, ...)` saves under 150 µs. Dismissed as below the level that repays the change.
- **I/O and Round Trips, caching**: The caches that exist are correct and bounded. `services/gateway/middleware/auth_cache.py` bounds by TTL and by `SESSION_CACHE_MAX` = 10,000 and handles the stale-key case in its eviction loop. `services/catalog/cache.py` uses a 60 s Redis TTL so price edits surface within a minute, which is a deliberate freshness trade-off rather than a missing cache. `services/catalog/categories.py:43` and `services/catalog/search/promoted.py:26` both reload hourly. `services/inventory/allocator.py:24` wraps the SHA-1 bin weight in an `lru_cache` of 8,192 entries.
- **I/O and Round Trips, batch paths**: Several paths already batch correctly and serve as the model for the N+1 fixes above: `services/catalog/search/hydrate.py:35-48` (one bulk price call plus a dict index), `services/inventory/reservations/batch.py:23-44` (array parameters, fixed statement count), `services/inventory/reservations/gc.py:54-62` (server-side cursor plus chunked deletes), `services/pricing/history.py:36-43` (one insert for each batch), `services/pricing/consumer/ack.py:13-17` (one XACK for each batch), `services/settlement/banking.py:33-45` (one bank call for the whole day), and `services/checkout/orders/txn.py:55` (`executemany` for the line items).