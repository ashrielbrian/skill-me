# Performance Audit Report

## Summary

bazaar is a marketplace monorepo. Six services share three libraries, one Postgres cluster, and Redis streams. The hot paths are the gateway edge at 1,200 req/s, catalog detail at 500 req/s, catalog search at 300 req/s, the pricing consumer at 200 events/s, and order placement at 13 orders/s.

Most of the code is careful. `services/inventory` already batches with array parameters, `libs/eventkit` already pipelines its writes, and most HTTP clients are module-level and shared. The waste sits in a small number of places where a list serves as a lookup table, or where a per-request cost hides inside a shared helper.

The single best quick win is one line. `services/catalog/search/promoted.py:31` builds a list of about 3,000 product ids, and `services/catalog/search/rank.py:26` scans that list 500 times per search request. A `frozenset` removes an estimated 30–60 ms from every search request.

## Findings

### [P1] The rank step scans a 3,000-id list 500 times per search request

- **Category**: Algorithms
- **Location**: `services/catalog/search/rank.py:23-31` (the test is on line 26); `services/catalog/search/promoted.py:18-33` (the list is built on line 31)
- **Hot path**: `services/catalog/routes.py:34` `search_handler` runs about 300 times per second. Line 47 reads the promoted ids. Line 48 calls `rank.rank_page`, which calls `rerank` at `rank.py:48`.
- **Problem**: `get_promoted_ids` returns a Python **list**. The README states the list holds about 3,000 product ids. `rerank` loops over the candidate set and runs `candidate["product_id"] in promoted_ids` for each candidate. `RANK_CANDIDATES` is 500 (`services/catalog/config.py:13`). Most candidates are not promoted, so most tests scan the whole list. The work is O(N x M) where a hash probe gives O(N).
- **Evidence**:
  ```python
  # rank.py:26
  if candidate["product_id"] in promoted_ids:
      score += _PROMOTED_BOOST
  ```
  ```python
  # promoted.py:31
  _promoted_ids = [row["product_id"] for row in rows]
  ```
- **Estimate**: This is an estimate. Assumptions: 500 candidates per request, 3,000 promoted ids, a full scan per miss, and 20–40 ns per list element in CPython.
  - Comparisons per request: 500 x 3,000 = 1,500,000.
  - Cost per request: 1,500,000 x 20–40 ns = 30–60 ms.
  - The search p50 is 30 ms, so this one step matches or exceeds the whole latency budget.
  - Fleet cost at 300 req/s: 9–18 CPU-seconds per second.
  - A `frozenset` needs 500 probes at about 50 ns: 25 µs per request. The saving is about 1,000x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Keep a `frozenset` next to the list in `promoted.py`. Rebuild the set inside the hourly reload block at `promoted.py:26-32`. Return both, and pass the set to `rerank`. Measure the search p50 before the change and after it.

### [P1] The rate limit middleware opens and parses a 2,000-entry file for each request

- **Category**: Redundant Work
- **Location**: `services/gateway/middleware/ratelimit.py:15-22` (the file read is on line 17, the parse on line 18)
- **Hot path**: `services/gateway/middleware/ratelimit.py:52-61` is a registered middleware (`services/gateway/app.py:26-32`). It runs for every request that reaches the edge, about 1,200 times per second. Line 56 calls `check(tenant_id)`.
- **Problem**: Two faults sit in one function. First, the rules file is loop-invariant data that the function re-reads and re-parses per request. The README states the file holds about 2,000 tenant entries. Second, `open()` and `read()` are blocking calls inside an aiohttp event loop, so one slow read stalls every other request in flight.
- **Evidence**:
  ```python
  # ratelimit.py:15-22
  def check(tenant_id):
      """Returns the per-minute budget for a tenant. Runs for each request."""
      with open(RATE_RULES_PATH) as f:
          rules = json.load(f)
  ```
- **Estimate**: This is an estimate. Assumptions: 2,000 entries at about 40 bytes each gives a file of about 80 KB. `json.load` runs at 50–100 MB/s on documents that hold many small objects.
  - Two syscalls plus a read of 80 KB from the page cache: about 20 µs.
  - Parse of 80 KB: 0.8–1.6 ms.
  - Total per request: about 0.85–1.6 ms, which is 11–20% of the 8 ms p50.
  - Fleet cost at 1,200 req/s: 1.0–1.9 CPU-seconds per second. One core cannot hold this rate.
  - A cache miss in the page cache costs one 4 KB read from SSD (20 µs) up to one disk seek (5 ms), and that time blocks the event loop.
  - A module-level dict makes the lookup one hash probe: about 50 ns. The saving is more than 10,000x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Load the rules once at import into a module-level dict. Add a reload on SIGHUP, or check the file mtime at most once every 10 seconds. Measure the gateway p50 and the process CPU share before and after.

### [P1] The auth middleware makes a blocking HTTP call inside the event loop

- **Category**: I/O and Round Trips
- **Location**: `services/gateway/handlers/session.py:28-30`
- **Hot path**: `services/gateway/middleware/auth.py:31-47` is a registered middleware. Line 41 and line 44 call `attach_session`. The README states about 70% of 1,200 req/s carry a session token, so about 840 req/s reach this code. Each cache miss makes the call.
- **Problem**: `attach_session` uses `urllib.request.urlopen`, which is synchronous. The gateway is a single-threaded aiohttp process, so the call stops the event loop for its whole duration. Every other request waits. `urlopen` also builds a new TCP connection per call, so there is no keep-alive. A shared `httpx.AsyncClient` already exists at `services/gateway/clients/auth_client.py:10`, but this path does not use it.
- **Evidence**:
  ```python
  # session.py:28-30
  raw = urllib.request.urlopen(
      AUTH_BASE_URL + "/v1/verify", data=payload, timeout=REQUEST_TIMEOUT_S
  )
  ```
- **Estimate**: This is an estimate. Assumptions: the README puts a sibling-service HTTP call at 5–20 ms. `SESSION_CACHE_TTL_S` is 300 and `SESSION_CACHE_MAX` is 10000 (`services/gateway/config.py:20-21`), so assume a 95% cache hit rate.
  - Misses per second: 5% of 840 = 42.
  - Event loop stall per second: 42 x 5–20 ms = 210–840 ms.
  - The loop therefore spends 21–84% of wall-clock time frozen. The p50 is 8 ms, so a single 10 ms stall pushes every request that arrives inside it past p50.
  - An awaited call over the pooled client costs the same 5–20 ms for that one request, but the other 1,199 requests per second continue. It also drops the per-call TCP handshake, which saves one more round trip.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add a `verify(token)` function to `services/gateway/clients/auth_client.py` next to `revoke_token`. Call it with `await` from `attach_session`. Measure the gateway p99 and the event loop lag before and after.

### [P1] The pricing consumer reads and decodes a 500 KB snapshot for every event

- **Category**: I/O and Round Trips
- **Location**: `services/pricing/consumer/apply.py:22`; `services/pricing/consumer/rules.py:8-16`
- **Hot path**: `services/pricing/consumer/loop.py:54` calls `apply_price_update` for each event in each batch. The README states 200 events/s per worker and about 5 ms per event at peak.
- **Problem**: `load_active_promos` runs one Redis GET and one `json.loads` for every event. The README states the snapshot is about 500 KB. The data changes at most once per hour, because `services/pricing/snapshots.py:50-57` publishes it hourly from the admin CLI. The consumer therefore decodes the same 500 KB up to 200 times per second.
- **Evidence**:
  ```python
  # apply.py:22
  promos = await load_active_promos(_redis)
  ```
  ```python
  # rules.py:13-16
  raw = await redis_client.get(PROMO_SNAPSHOT_KEY)
  if raw is None:
      return {}
  return json.loads(raw)
  ```
- **Estimate**: This is an estimate. Assumptions: a 500 KB snapshot, Redis operations at 0.2–1 ms per the README, and `json.loads` at 100–150 MB/s on object-heavy documents.
  - Redis GET plus transfer of 500 KB: about 1 ms.
  - `json.loads` of 500 KB: 3.3–5.0 ms.
  - Total per event: 4.3–6.0 ms against a 5 ms budget. One worker cannot hold 200 events/s.
  - Redis traffic: 200 x 500 KB = 100 MB/s per worker.
  - One load per batch of 100 cuts the count from 200 per second to 2 per second. That is about 100x. One load per hour cuts it further.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Cache the decoded dict in the `rules` module behind a refresh interval. Pass the cached dict into `apply_price_update` from `run_loop`. Measure the `p99_ms` value that the batch log line already reports at `loop.py:64`.

### [P1] The campaign match step scans a 2,000-SKU list up to 30 times per event

- **Category**: Algorithms
- **Location**: `services/pricing/consumer/match.py:4-13` (the test is on line 11); `services/pricing/campaigns.py:17-20` (the list is built on line 20)
- **Hot path**: `services/pricing/consumer/loop.py:53` calls `find_affected_skus` for each event, at 200 events/s. `loop.py:34` loads the roster once at worker start.
- **Problem**: `load_campaign_skus` returns a **list**. Its docstring states about 2,000 SKUs are on campaign at a time. `find_affected_skus` tests each candidate SKU against that list. Its docstring states events carry up to about 30 candidate SKUs. The only consumer of the result is the `campaign_hits` counter in the batch log line at `loop.py:61-64`.
- **Evidence**:
  ```python
  # match.py:10-12
  for sku in event.candidate_skus:
      if sku in campaign_skus:
          affected.append(sku)
  ```
- **Estimate**: This is an estimate. Assumptions: 30 candidate SKUs, 2,000 roster entries, a full scan per miss, and 30–50 ns per string comparison in a list scan.
  - Comparisons per event: 30 x 2,000 = 60,000.
  - Cost per event: 60,000 x 30–50 ns = 1.8–3.0 ms.
  - That is 36–60% of the 5 ms per-event budget, spent to produce one log counter.
  - Fleet cost at 200 events/s: 0.36–0.60 CPU-seconds per second per worker.
  - A `set` needs 30 probes at about 60 ns: about 2 µs. The saving is about 1,000x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Return a `set` from `load_campaign_skus`, or wrap the result in `set()` at `loop.py:34`. Keep `roster_digest` working, because it reads `skus[0]`; pass it the list, or change it to report only the count. Measure the reported `p99_ms` per event.

### [P1] Order placement makes one HTTP call per line item where a batch endpoint exists

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/reserve_step.py:16-35` (the call is on line 23)
- **Hot path**: `services/checkout/orders/create.py:47` calls `reserve_items` inside `place_order`. `services/checkout/routes.py:40` serves it at about 13 orders/s. The README states orders average 8 line items and the order p50 budget is 90 ms.
- **Problem**: The loop posts to `/v1/reserve` once per line item and awaits each call before it starts the next. The inventory service already exposes `/v1/reserve/batch` for up to 100 SKUs in one call and one transaction. See `services/inventory/routes.py:46` and `services/inventory/reservations/batch.py:47`. The checkout service does not use it. `MAX_LINE_ITEMS` is 100 (`services/checkout/config.py:10`), so the worst case is 100 sequential calls.
- **Evidence**:
  ```python
  # reserve_step.py:22-30
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
- **Estimate**: This is an estimate. Assumptions: 8 line items per order, and 5–20 ms per sibling-service HTTP call per the README.
  - Today, average order: 8 x 5–20 ms = 40–160 ms. The p50 budget is 90 ms.
  - Today, 100-item order: 100 x 5–20 ms = 500–2,000 ms.
  - One batch call: 5–20 ms.
  - Saving on an average order: 35–140 ms, which is about 8x on this step.
  - The loop also opens 8 transactions inside inventory instead of 1, so the rollback path at `reserve_step.py:38-41` must undo partial holds.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace the loop with one POST to `/v1/reserve/batch`. Send the order id, the zone, and every line in one payload. Delete the partial-release path, because the batch endpoint is atomic. Measure the order p50 and p99.

### [P1] Search hydration runs one seller query per page row

- **Category**: I/O and Round Trips
- **Location**: `services/catalog/search/hydrate.py:11-27` (the query is on line 17)
- **Hot path**: `services/catalog/routes.py:49` calls `attach_sellers` for each search response, at about 300 req/s. A page holds `SEARCH_PAGE_SIZE` rows, which is 25 (`services/catalog/config.py:12`).
- **Problem**: The loop awaits one `fetch_one` per row. This is an N+1 pattern. The same module already shows the correct shape: `attach_prices` at `hydrate.py:35-48` makes one batch call and then indexes the reply by product id.
- **Evidence**:
  ```python
  # hydrate.py:16-20
  for row in rows:
      seller = await fetch_one(
          "SELECT display_name, badge FROM sellers WHERE id = $1",
          (row["seller_id"],),
      )
  ```
- **Estimate**: This is an estimate. Assumptions: 25 rows per page, and 0.5–2 ms per simple indexed query round trip per the README.
  - Today: 25 x 0.5–2 ms = 12.5–50 ms per search request. The search p50 is 30 ms.
  - Query rate at 300 req/s: 7,500 seller queries per second.
  - Each query takes a pool connection, and `POOL_MAX` is 20 (`libs/dbkit/config.py:13`). At 7,500 queries per second the pool becomes the limit.
  - One query with `WHERE id = ANY($1)`: 0.5–2 ms.
  - Saving: 12–48 ms per request, which is about 25x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Collect the distinct seller ids from the page. Run one query with `WHERE id = ANY($1)`. Index the reply by id, then fill each row from that dict. Copy the shape of `attach_prices`. Measure the search p50 and the pool wait time.

### [P1] The shared query helper serializes every result set for a log call that is off

- **Category**: Observability Overhead
- **Location**: `libs/dbkit/executor.py:23`
- **Hot path**: Every `fetch_all` caller in all six services goes through this line. The hottest caller is `services/catalog/search/query.py:69`, which returns `RANK_CANDIDATES` = 500 rows per search request at 300 req/s. The largest single caller is `services/settlement/cron.py:43`, which loads about 1.2M order rows in one call.
- **Problem**: `json.dumps(rows, default=str)` sits in the argument list, so Python evaluates it before the call. The `logging` module then discards the record, because every process starts at INFO. See `services/gateway/app.py:41`, `services/catalog/app.py:41`, `services/pricing/worker.py:40`, and `services/settlement/cron.py:77`. The result is that each query serializes its full result set to a JSON string and throws the string away.
- **Evidence**:
  ```python
  # executor.py:22-24
  rows = [dict(record) for record in records]
  logger.debug("rows=%s", json.dumps(rows, default=str))
  return rows
  ```
- **Estimate**: This is an estimate. Assumptions: `json.dumps` with a `default` hook runs at 50–100 MB/s, and one search row of 8 columns renders as about 200 bytes of JSON.
  - Catalog search: 500 rows x 200 bytes = about 100 KB per request. Cost: 1.0–2.0 ms per request, which is 3–7% of the 30 ms search p50.
  - Fleet cost at 300 req/s: 0.3–0.6 CPU-seconds per second, plus about 30 MB/s of garbage.
  - Settlement: 1.2M rows at about 130 bytes each is about 156 MB of JSON text. Cost: 1.6–3.1 seconds of CPU and about 156 MB of peak memory, on top of the 1.2M row dicts.
  - A level check costs about 50 ns, so the saving is the full serialization cost.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the call in `if logger.isEnabledFor(logging.DEBUG):`, or delete the line. Check `fetch_one` and `execute` for the same pattern; they are clean today. Measure the catalog search p50 and the settlement pass duration.

### [P1] The tax lookup scans 5,100 rules on every call

- **Category**: Algorithms
- **Location**: `libs/commerce/tax.py:16-24` (the rule list) and `libs/commerce/tax.py:27-32` (the scan)
- **Hot path**: Two hot callers use it.
  - `services/pricing/consumer/apply.py:32` calls `rate_for` once per event, at 200 events/s.
  - `services/checkout/orders/pricing_step.py:29` calls `rate_for` once per line item, so 8 times per order at 13 orders/s.
- **Problem**: `_RULES` is a list of `len(REGIONS)` x `len(CATEGORIES)` tuples. `REGIONS` holds 150 codes (`libs/commerce/regions.py:8-24`) and `CATEGORIES` holds 34 names (`libs/commerce/catalog_types.py:8-16`), so the list holds 5,100 entries. This matches the README figure of about 5,000 rows. `rate_for` walks the list and compares two strings per entry. A dict keyed by `(region, category)` answers the same question in one hash probe.

  One further fact makes the worst case normal for pricing. `services/pricing/config.py:33-39` uses lowercase region keys such as `"us"` and `"eu"`, while `REGIONS` holds uppercase codes such as `"US"`. Pricing lookups therefore never match, scan all 5,100 entries, and return `DEFAULT_RATE`.
- **Evidence**:
  ```python
  # tax.py:29-32
  for rule_region, rule_category, rate in _RULES:
      if rule_region == region and rule_category == category:
          return rate
  return DEFAULT_RATE
  ```
- **Estimate**: This is an estimate. Assumptions: 5,100 entries, and 50–120 ns per iteration for one tuple unpack plus up to two short string comparisons.
  - Full scan: 5,100 x 50–120 ns = 255–612 µs.
  - Pricing, per event: 255–612 µs, which is 5–12% of the 5 ms budget. Fleet cost at 200 events/s: 0.05–0.12 CPU-seconds per second per worker.
  - Checkout, per order: 8 x 255–612 µs = 2.0–4.9 ms, which is 2–5% of the 90 ms budget.
  - A dict probe costs about 60 ns. The saving is about 4,000–10,000x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Build `_RULES_BY_KEY = {(region, category): rate}` at import, next to `_RULES`. Change `rate_for` to one `.get()` call with `DEFAULT_RATE` as the fallback. Keep `_RULES` and `rule_count()`, because the health report reads the count. Also fix the region case mismatch in pricing, or the dict returns the default too. Measure the reported `p99_ms` per event and the order p50.

### [P1] Webhook delivery builds a new HTTP client per subscriber and sends in sequence

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/webhooks/notify.py:14-23` (the client is built on line 19) and `services/checkout/webhooks/notify.py:26-50` (the sequential loop is on lines 36-41)
- **Hot path**: `services/checkout/orders/create.py:52` awaits `dispatch` inside `place_order`, before the handler returns to the client. `services/checkout/routes.py:40` serves it at about 13 orders/s. `cancel_order` at `create.py:71` uses the same path.
- **Problem**: Two faults compound here.
  - `send_order_event` opens a new `httpx.AsyncClient` for each delivery, so each callback pays a fresh TCP handshake and a fresh TLS handshake. Every other client in the repository is module-level, for example `services/checkout/clients/inventory_client.py:12`.
  - `dispatch` awaits each delivery before it starts the next, so the total cost is the sum, not the maximum.
  
  `registry.add_subscription` at `services/checkout/webhooks/registry.py:39` requires `https` and an absolute host, so subscriber endpoints are external. `WEBHOOK_TIMEOUT_S` is 5 (`services/checkout/config.py:9`), so one slow endpoint can hold the order request for 5 seconds.
- **Evidence**:
  ```python
  # notify.py:19-20
  async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
      response = await client.post(url, json=event)
  ```
- **Estimate**: This is an estimate. Assumptions: 3 active subscribers per topic, and a 50 ms round trip to an external endpoint. The subscriber count is not in the code or the README, so scale the numbers with the real count.
  - Handshake per delivery: about 3 round trips, so about 150 ms.
  - Request per delivery: about 50 ms.
  - Today: 3 x 200 ms = 600 ms added inside a 90 ms p50 budget.
  - One shared client removes the handshake on later calls: 3 x 50 ms = 150 ms.
  - `asyncio.gather` makes the fan-out cost one delivery: about 50 ms.
  - Saving: about 550 ms per order, which is about 12x on this step.
- **Impact**: High
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Move the client to module level, next to the `logger`. Close it from the shutdown hook. Replace the loop with `asyncio.gather` over the subscriber URLs, and keep `return_exceptions=True` so one bad endpoint does not cancel the rest. Measure the order p50 and p99. If the p99 is still wide, move `dispatch` to a background task, which is Medium effort.

### [P2] The pricing step runs one price query per line item

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/pricing_step.py:18-33` (the query is on line 25)
- **Hot path**: `services/checkout/orders/create.py:43` calls `price_items` once per order, at about 13 orders/s. Orders average 8 line items and carry up to 100 (`services/checkout/config.py:10`).
- **Problem**: The loop awaits one `fetch_one` per line item. This is an N+1 pattern. One query with `WHERE product_id = ANY($1)` returns every price in one round trip.
- **Evidence**:
  ```python
  # pricing_step.py:24-27
  for item in items:
      row = await fetch_one(_PRICE_QUERY, (item.product_id,))
      if row is None:
          raise PricingFailure(item.product_id)
  ```
- **Estimate**: This is an estimate. Assumptions: 8 line items, and 0.5–2 ms per indexed query round trip per the README.
  - Today, average order: 8 x 0.5–2 ms = 4–16 ms, which is 4–18% of the 90 ms budget.
  - Today, 100-item order: 50–200 ms.
  - One query: 0.5–2 ms.
  - Saving on an average order: 3.5–14 ms, which is about 8x on this step.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Fetch every price in one query with `ANY($1)`. Index the rows by product id. Raise `PricingFailure` for the first product id that the dict does not hold, so the error stays the same. Measure the order p50.

### [P2] The latency window sorts 2,048 samples on every event

- **Category**: Observability Overhead
- **Location**: `services/pricing/consumer/stats.py:15-20` (the sort is on line 18)
- **Hot path**: `services/pricing/consumer/loop.py:56` calls `tracker.record` once per applied event, at 200 events/s. `loop.py:64` reads `p99_ms` once per batch.
- **Problem**: `record` sorts the whole window to refresh the p99, but the p99 is read only once per batch. `STATS_WINDOW` is 2048 (`services/pricing/config.py:23`) and `STREAM_READ_COUNT` is 100 (`services/pricing/config.py:17`). The worker therefore performs 100 sorts per batch where 1 sort answers the same question.
- **Evidence**:
  ```python
  # stats.py:17-20
  self._window.append(value)
  ordered = sorted(self._window)
  rank = max(0, int(len(ordered) * 0.99) - 1)
  self.p99_ms = ordered[rank]
  ```
- **Estimate**: This is an estimate. Assumptions: `sorted()` over 2,048 floats copies the deque and runs about 22,500 comparisons.
  - Cost per event: 150–250 µs, which is 3–5% of the 5 ms budget.
  - Cost per batch of 100: 15–25 ms.
  - Fleet cost at 200 events/s: 0.03–0.05 CPU-seconds per second per worker.
  - One sort per batch cuts 99 of every 100 sorts. The saving is about 100x on this step.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Make `record` append the sample only. Move the sort and the rank arithmetic into `summary()`. Keep the `p99_ms` attribute, because `reset()` writes it and the tests may read it.

### [P2] The apply step wraps one statement in an explicit transaction per event

- **Category**: I/O and Round Trips
- **Location**: `services/pricing/consumer/apply.py:34-46`
- **Hot path**: `services/pricing/consumer/loop.py:54` calls `apply_price_update` once per event, at 200 events/s with about 5 ms per event.
- **Problem**: `conn.transaction()` wraps a single `UPDATE`. asyncpg already runs a lone statement inside an implicit transaction, so the wrapper adds a BEGIN round trip and a COMMIT round trip and buys nothing. The COMMIT is also a durable write. The rule of thumb is 1–10 ms per commit, which caps one worker at a few hundred events per second.
- **Evidence**:
  ```python
  # apply.py:34-36
  async with (await get_pool()).acquire() as conn:
      async with conn.transaction():
          await conn.execute(
  ```
- **Estimate**: This is an estimate. Assumptions: 0.5–2 ms per round trip inside the datacenter per the README.
  - Extra round trips per event: 2 (BEGIN and COMMIT).
  - Extra cost per event: 1–4 ms, against a 5 ms budget.
  - Extra round trips per second per worker: 400.
  - The batch already flushes history rows in one insert (`services/pricing/history.py:36-43`) and acknowledges the whole batch with one XACK (`services/pricing/consumer/ack.py:13-17`). The price update is the one step that stays per event.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Remove the `conn.transaction()` wrapper and keep the single `execute`. For a larger win, collect the 100 update tuples per batch and apply them with one `executemany` inside one transaction; that is Medium effort. Measure the reported `p99_ms` per event and the batch duration.

### [P2] Order context awaits three independent queries in sequence

- **Category**: I/O and Round Trips
- **Location**: `services/checkout/orders/context.py:20-22`
- **Hot path**: `services/checkout/orders/create.py:40` calls `load_order_context` once per order, at about 13 orders/s.
- **Problem**: The buyer read, the cart read, and the address read do not depend on each other, but each `await` blocks the next. `asyncio.gather` sends all three at once, so the cost falls to the slowest of the three.
- **Evidence**:
  ```python
  # context.py:20-22
  buyer = await fetch_one(_BUYER_QUERY, (buyer_id,))
  cart = await fetch_one(_CART_QUERY, (cart_id,))
  address = await fetch_one(_ADDRESS_QUERY, (buyer_id,))
  ```
- **Estimate**: This is an estimate. Assumptions: 0.5–2 ms per indexed query round trip per the README.
  - Today: 3 x 0.5–2 ms = 1.5–6 ms.
  - With `gather`: 0.5–2 ms.
  - Saving per order: 1–4 ms, which is 1–4% of the 90 ms budget, and about 3x on this step.
  - The three calls take three pool connections at the same time instead of one after another. `POOL_MAX` is 20, and this path runs 13 times per second, so the pool has room.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Wrap the three calls in `asyncio.gather`. Keep the checks at lines 24-33 in the same order, so the error messages and their priority do not change. Measure the order p50.

### [P2] The detail query selects a 20 KB column that the view never reads

- **Category**: Allocation and Copies
- **Location**: `services/catalog/products/repo.py:11-19` (the statement is on line 17)
- **Hot path**: `services/catalog/routes.py:67` calls `repo.get_product` for each detail request that misses the Redis cache. Detail traffic is about 500 req/s, and `DETAIL_CACHE_TTL_S` is 60 (`services/catalog/config.py:16`).
- **Problem**: `get_product` uses `SELECT *`. `views.detail_view` at `services/catalog/products/views.py:21-30` reads exactly 8 fields. The README states a typical `description_html` column is about 20 KB. Postgres must detoast that value and send it over the wire, and asyncpg must decode it, so that the code can discard it. The two sibling modules already name their columns: see `services/catalog/products/related.py:9` and `services/catalog/products/repo.py:22-25`.
- **Evidence**:
  ```python
  # repo.py:16-19
  return await fetch_one(
      "SELECT * FROM products WHERE id = $1",
      (product_id,),
  )
  ```
- **Estimate**: This is an estimate. Assumptions: 500 detail req/s, a 90% cache hit rate under a 60 s TTL, and a 20 KB `description_html` value.
  - Uncached reads per second: 50.
  - Wasted bytes per second: 50 x 20 KB = 1 MB/s over the wire and through the decoder.
  - Added cost per uncached read: about 0.2 ms for the transfer, plus one TOAST read on the server side.
  - Named columns remove all of it. The saving is about 0.2 ms per uncached read and 1 MB/s of database traffic.
- **Impact**: Medium
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Name the 8 columns that `detail_view` reads. Reuse `_LISTING_COLUMNS` at `repo.py:22-25`, and add `description_html` only where a caller needs it. Confirm that no other caller of `get_product` depends on an unnamed column. Measure the detail p50 and the row bytes on the connection.

### [P3] The access log formats JSON for a log record that the process discards

- **Category**: Observability Overhead
- **Location**: `services/gateway/middleware/accesslog.py:18-20` (the format is on line 20)
- **Hot path**: `services/gateway/middleware/accesslog.py:23-42` is the first middleware in the chain (`services/gateway/app.py:26-32`). Line 30 calls `log_request` for every completed request, about 1,200 times per second.
- **Problem**: `json.dumps(context)` sits in the argument list of a `logger.debug` call. The gateway starts at INFO (`services/gateway/app.py:41`), so the record never reaches a handler, but the format still runs. This is the same pattern as `libs/dbkit/executor.py:23`, at a smaller size.
- **Evidence**:
  ```python
  # accesslog.py:20
  logger.debug("request %s", json.dumps(context))
  ```
- **Estimate**: This is an estimate. Assumptions: a 7-key flat dict, and 3–6 µs per `json.dumps` call at that size.
  - Cost per request: 3–6 µs, which is about 0.06% of the 8 ms p50.
  - Fleet cost at 1,200 req/s: 3.6–7.2 ms of CPU per second.
  - The saving is real but small, so this is worth doing only when the executor fix touches the same pattern.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Guard the call with `if logger.isEnabledFor(logging.DEBUG):`, or pass `context` as a lazy argument and let a JSON formatter render it.

### [P3] Facet aggregation repeats dict lookups and string conversions per row and per field

- **Category**: Redundant Work
- **Location**: `services/catalog/search/facets.py:29-40`; `services/catalog/search/facets.py:22-26`
- **Hot path**: `services/catalog/routes.py:53` calls `facet_counts` over the full candidate set for each search response, at 300 req/s. `RANK_CANDIDATES` is 500. Line 54 then calls `annotate_rows`, which calls `_facet_value` 4 more times per page row.
- **Problem**: Three small costs repeat per row and per field.
  - `_facet_value` calls `str(row.get(field, ""))` even when the column is already text.
  - `rating_band` walks 4 band edges for every row.
  - The counter runs `value not in counts[field]` and then two more index operations, where `counts[field].get(value, 0) + 1` needs one lookup.
- **Evidence**:
  ```python
  # facets.py:34-39
  for row in rows:
      for field in FACET_FIELDS:
          value = _facet_value(row, field)
          if value not in counts[field]:
              counts[field][value] = 0
          counts[field][value] = counts[field][value] + 1
  ```
- **Estimate**: This is an estimate. Assumptions: 500 candidates, 4 facet fields, and 400–600 ns per (row, field) pair in CPython.
  - Calls per request: 500 x 4 = 2,000.
  - Cost per request: 0.8–1.2 ms, which is 3–4% of the 30 ms search p50.
  - Fleet cost at 300 req/s: 0.24–0.36 CPU-seconds per second.
  - A tighter loop saves about half of it: about 0.4–0.6 ms per request, or 1–2% of the path.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Use `collections.Counter`, or replace the three index operations with one `get`. Hoist the `counts[field]` lookup out of the row loop. Skip `str()` for columns that are already text. Measure the search p50.

### [P3] Currency conversion re-parses its rate table on every call

- **Category**: Allocation and Copies
- **Location**: `libs/commerce/money.py:53`; the JSON text is at `libs/commerce/money.py:17-48`
- **Hot path**: Two hot callers use it.
  - `services/pricing/consumer/apply.py:25`, `:29`, and `:31` call `convert` 2 or 3 times per event, at 200 events/s.
  - `services/checkout/orders/pricing_step.py:28` calls `convert` once per line item, so 8 times per order.
- **Problem**: `convert` calls `json.loads(_RATES_JSON)` on every call. The text holds 150 currency rates and never changes at run time. The module comment at lines 62-69 states that the display quantize constants are built once and shared, which is the correct pattern; the rate table does not follow it.
- **Evidence**:
  ```python
  # money.py:51-53
  def convert(amount_cents, from_ccy, to_ccy) -> int:
      """Convert an integer minor-unit amount between currencies."""
      rates = json.loads(_RATES_JSON)
  ```
- **Estimate**: This is an estimate. Assumptions: the text is about 2.3 KB with 150 keys, and `json.loads` costs 20–30 µs at that size.
  - Pricing, per event: 2 or 3 calls give 40–90 µs, which is 1–2% of the 5 ms budget.
  - Fleet cost at 200 events/s: 8–18 ms of CPU per second, plus 400–600 discarded dicts per second.
  - Checkout, per order: 8 x 20–30 µs = 160–240 µs.
  - A module-level dict makes the cost two hash probes, about 100 ns. The saving is about 250x on this step.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Add `_RATES = json.loads(_RATES_JSON)` at module level, below the text. Read `_RATES` inside `convert`. Keep `_RATES_JSON` if a test asserts on it.

### [P3] The audit context walks the whole call stack, and each record walks it twice

- **Category**: Redundant Work
- **Location**: `libs/commerce/audit.py:26-40` (the walk is on line 27); `libs/commerce/audit.py:43-52` (the second walk is on line 48)
- **Hot path**: Two callers use it.
  - `services/checkout/orders/create.py:50` writes one record per placed order, at about 13 orders/s.
  - `services/settlement/payouts/ledger.py:48` writes one record per ledger batch. `LEDGER_BATCH_SIZE` is 1000 (`services/settlement/config.py:15`).
- **Problem**: `audit_context` calls `traceback.extract_stack()`, which walks every frame and resolves source lines through `linecache`. The function needs one frame: the nearest caller outside the package. Also, `create.py:50` builds a context and passes it in, and then `write_audit` builds a second context at line 48 and overwrites the caller's fields. So each placed order walks the stack twice.
- **Evidence**:
  ```python
  # audit.py:26-33
  def audit_context() -> dict:
      """Build the shared context fields for one audit record."""
      frames = traceback.extract_stack()
      caller = "unknown"
  ```
  ```python
  # create.py:50
  await write_audit(audit_context())
  ```
- **Estimate**: This is an estimate. Assumptions: an aiohttp request stack runs 20–40 frames deep, and `extract_stack` costs 50–200 µs at that depth after linecache warms up.
  - Cost per placed order: 2 walks x 50–200 µs = 100–400 µs, which is 0.1–0.4% of the 90 ms budget.
  - Cost per settlement run: 1.2M rows / 1,000 per batch gives about 1,200 batches, so 2,400 walks, or 0.12–0.48 seconds.
  - `sys._getframe(1)` reads one frame in about 1 µs. Removal of the duplicate call halves the count. Together the saving is about 100x on this step.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Walk one frame with `sys._getframe`, or accept the caller tag as a parameter. Delete the second `audit_context()` call at `audit.py:48` and use the record that the caller passed in. Note that `services/gateway/handlers/orders_proxy.py:27-28` calls both functions with a different signature, so check that call site during the change.

### [P3] The nightly report runs the same aggregate query three times

- **Category**: Redundant Work
- **Location**: `services/settlement/reports/daily.py:17-22`
- **Hot path**: `services/settlement/cron.py:57` calls `render_report` once per settlement run, which the README states is once per day at 02:00 UTC.
- **Problem**: `render_report` awaits `summarize_fees(conn, day)` three times with identical arguments. Each call is one round trip plus one GROUP BY scan of the day's `payout_ledger` rows. One call answers all three sections.
- **Evidence**:
  ```python
  # daily.py:19-21
  header = _header(await summarize_fees(conn, day), day)
  body = _body(await summarize_fees(conn, day))
  footer = _footer(await summarize_fees(conn, day))
  ```
- **Estimate**: This is an estimate. Assumptions: 0.5–2 ms per round trip, plus the aggregate scan time.
  - Extra round trips: 2 per run.
  - Extra cost: 1–4 ms plus 2 aggregate scans, once per day.
  - The absolute saving is small, because the code runs once per day inside a wide window. The fix is one line, so take it when the file is next touched.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Assign `fee_rows = await summarize_fees(conn, day)` once, then pass it to `_header`, `_body`, and `_footer`.

### [P3] The nightly summary sorts 1.2M rows to take 50

- **Category**: Algorithms
- **Location**: `services/settlement/payouts/rollup.py:30-33` (the sort is on line 32)
- **Hot path**: `services/settlement/cron.py:58` calls `top_sellers(rows)` once per settlement run. `rows` comes from `cron.py:43`, which the README states holds about 1.2M order rows.
- **Problem**: The function sorts every row and then keeps `TOP_SELLERS_LIMIT`, which is 50 (`services/settlement/config.py:12`). This is a full sort used only to find the top k. `heapq.nlargest` makes one pass and holds a 50-element heap.
- **Evidence**:
  ```python
  # rollup.py:32-33
  ordered = sorted(rows, key=lambda r: r["net_cents"], reverse=True)
  return ordered[:TOP_SELLERS_LIMIT]
  ```
- **Estimate**: This is an estimate. Assumptions: 1.2M rows, and about 1.2M x log2(1.2M) = 24M comparisons, each with one key-function call.
  - `sorted`: 1.5–3.0 seconds, plus a 1.2M-element list copy of about 10 MB of pointers.
  - `heapq.nlargest(50, ...)`: 0.1–0.2 seconds and no large copy.
  - Saving: 1.4–2.8 seconds once per night, which is about 15x on this step.
  - The saving is real but the run happens once per day inside a wide window, so the priority stays low.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Replace `sorted(...)[:limit]` with `heapq.nlargest(TOP_SELLERS_LIMIT, rows, key=...)`. Profile the settlement pass first if you plan further work on this file.

### [P3] The insert helper rebuilds an identical placeholder text for every flush

- **Category**: Redundant Work
- **Location**: `libs/dbkit/batch.py:15-26`; the caller loop is at `libs/dbkit/batch.py:41-46`
- **Hot path**: Two callers use it.
  - `services/pricing/history.py:41` writes one batch per consumed batch, so about 2 statements per second per worker with up to 100 rows each.
  - `services/settlement/payouts/ledger.py:47` writes about 1,200 batches per nightly run, chunked further to `FLUSH_ROWS` = 500 rows.
- **Problem**: `build_values_clause` formats one `$n` string per column per row, joins each row group, and appends with `+=`. For chunks of the same width and row count the text is identical every time, so a cache keyed by `(width, row_count)` answers it once. The `+=` growth also relies on the CPython in-place string optimization, which is not part of the language guarantee.
- **Evidence**:
  ```python
  # batch.py:20-25
  for row_index in range(len(rows)):
      group = ", ".join("$%d" % (position + offset) for offset in range(width))
      if row_index:
          clause += ", "
      clause += "(" + group + ")"
  ```
- **Estimate**: This is an estimate. Assumptions: a full 500-row chunk of 5 columns needs 2,500 `%` formats and 500 `join` calls, at 0.5–1.0 ms per statement.
  - Pricing: about 2 statements per second, so about 1–2 ms of CPU per second. Negligible.
  - Settlement: about 2,400 full chunks per run, so 1.2–2.4 seconds per night.
  - A cache removes almost all of it, because every full chunk shares one text.
- **Impact**: Low
- **Effort**: Small (< 1 hour)
- **Suggested fix**: Memoize the clause on `(width, len(rows))` in a module-level dict. Use a list plus `"".join` instead of `+=` so the growth does not depend on an interpreter detail. Measure the settlement pass duration.

## Cold-Code Notes

- `services/gateway/handlers/export.py:24` — one actor query per audit event, inside a loop over every audit row for a tenant. The route table at `services/gateway/routes.py:14-27` does not register `export_activity`, and no module imports it, so nothing reaches this code today. If someone wires it up, it becomes a P1 N+1 finding: one round trip of 0.5–2 ms per event row.
- `services/catalog/search/semantic.py:38-41` — `semantic_rescore` compares every candidate against every reference vector with a Python-level dot product over `EMBEDDING_DIM` = 64 components. `config.SEMANTIC_RESCORE` is `False` (`services/catalog/config.py:14`), so `rank.py:49` never enters the branch. The latent cost is large: at 500 candidates and 100 reference vectors that is 3.2M multiply-add steps, or an estimated 0.3–1.0 s per search request. The README states the beta is for allow-listed tenants, but the gate is one global constant, so a flag change turns the code on for all traffic at once.
- `services/settlement/cron.py:43` — one `fetch_all` loads about 1.2M order rows into memory as dicts before any aggregation. `libs/dbkit/cursor.py` already offers `fetch_page` for keyset pagination, and `rollup_by_seller` needs only one pass. The peak memory is an estimated 0.5–1.0 GB for the row dicts alone.
- `libs/commerce/flags.py:36` — `re.compile` runs inside the parse loop, once per flag line. `load_flag_file` runs once at import (line 45) over about 30 entries, so the total cost is under 100 µs.
- `libs/commerce/skuid.py:22` — `str.maketrans` builds a 9-entry translation table on every `normalize_sku` call. The only production caller is `services/catalog/products/repo.py:32`, whose docstring says the admin CLI calls it. The dict also lists `" ": "-"` twice, so one entry is dead.
- `libs/dbkit/prepare.py:26-29` — `re.sub` runs once per named parameter, so an N-parameter template rewrites the statement N times. No module in the repository calls `render_sql`.
- `libs/dbkit/metrics.py` and `libs/eventkit/metrics.py` — both modules take a `threading.Lock` per update, but no service module calls `incr`, `observe`, `timed`, or any `note_*` function. The counters are defined and never collected.
- `services/settlement/reports/render.py:10` — `format_money_rows` builds a `decimal.Context` inside the row loop. The caller aggregates by plan, and `FEE_SCHEDULE` lists 3 plans, so the loop runs 3 times per night.
- `services/catalog/search/spelling.py:20-29` — `_single_edits` yields about 53 candidates per character position, so a 10-character term generates about 570 strings. Nothing in `services/catalog/routes.py` calls `correct` or `correct_all`.
- `tools/backfill_sellers.py:36-51` — `match_legacy_rows` nests a loop over current sellers inside a loop over legacy sellers, so the work is O(N x M). A dict keyed by email and a second dict keyed by `(display_name, signup_date)` make it O(N + M). This is a one-shot migration script for release 2.8.

## Not Found

- **Data Layout** — no findings. The reference data already uses the right shapes: `frozenset` for the currency, region, category, status, and topic sets; `dataclass(frozen=True, slots=True)` for `SessionContext` and `TenantRecord` (`services/gateway/models.py:5`, `:24`); and a precomputed index dict at `services/catalog/products/attributes.py:33`. No nested map costs several lookups per access on a hot path.
- **`services/catalog/search/normalize.py:18`** — checked and dismissed. `re.compile` sits inside a per-request function, but CPython caches compiled patterns in a 512-entry table, so the repeat cost is a cache lookup of about 1–2 µs, not a compile. At 300 req/s that is 0.45 ms of CPU per second, or about 0.005% of the 30 ms search p50.
- **`services/checkout/carts/repo.py:44-45`, `:50-51`, `:56-57`** — checked and dismissed. Each function awaits a write and then awaits `_TOUCH_CART`, so it pays 2 round trips of 0.5–2 ms where 1 would do. The cart-edit path runs a few dozen times per second and has no stated latency budget, so the 0.5–2 ms saving does not repay the change today.
- **`services/inventory/allocator.py:24-42`** — checked and dismissed. `bin_weight` runs a SHA-1 digest plus an 8-round float loop per SKU, but `functools.lru_cache(maxsize=8192)` already caches the result, and the roster is about 2,000 SKUs. Later calls cost one dict lookup.
- **`services/inventory/reservations/batch.py:47-91`** — checked and dismissed as already correct. The batch path locks with `sku = ANY($1)`, decrements through `unnest`, and inserts through `unnest`, all inside one transaction. It is 3 round trips for up to 100 SKUs, and it is the model that `services/checkout/orders/reserve_step.py` should follow.
- **`libs/eventkit/publisher.py:37-52`** — checked and dismissed as already correct. The producer buffers up to `PUBLISH_FLUSH_COUNT` = 200 events and delivers them in one pipelined round trip. `libs/eventkit/codec.py:26-31` also builds one encoder and one decoder at import, rather than per call.
- **`services/pricing/consumer/dedupe.py:17-28`** — checked and dismissed. `DedupeWindow` uses an `OrderedDict` with `move_to_end` and `popitem`, so `seen_before` is O(1) at `DEDUPE_CAPACITY` = 8192. A list would have been O(N).
- **`libs/dbkit/pool.py:30-49`** — checked and dismissed as already correct. One pool per process, created on first use, with a double-checked lock. No module builds a connection per call.
- **Module-level HTTP clients** — checked and dismissed for `services/gateway/clients/auth_client.py:10`, `services/gateway/clients/catalog_client.py:10`, `services/gateway/handlers/orders_proxy.py:13`, `services/catalog/clients/pricing_client.py:12`, `services/checkout/clients/inventory_client.py:12`, `services/checkout/orders/reserve_step.py:13`, and `services/settlement/banking.py:16`. All seven share one connection pool per process. Only `services/checkout/webhooks/notify.py:19` breaks the pattern, and that is a P1 finding above.
- **String concatenation in a loop** — one real instance, at `libs/dbkit/batch.py:23-24`, reported as a P3 finding. `services/pricing/consumer/decode.py:18-22` also uses `+=`, but it appends 4 short parts to build one routing key, which costs an estimated 300 ns per event.