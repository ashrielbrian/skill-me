# pricing

Consumes ~12,000 events/min (200/s) from Redis streams; each worker has about 5 ms per event at peak.

## Layout

- `worker.py` — process entry point; builds the Redis client, ensures the
  consumer group, and runs the loop.
- `consumer/` — the pipeline stages: decode, dedupe, match, apply, stats,
  ack.
- `campaigns.py` — campaign SKU roster, loaded when a worker starts.
- `snapshots.py` — publishes the promo ruleset to Redis; run hourly by
  the admin CLI, not by workers.
- `history.py` — price history rows, buffered per batch and written with
  one insert per batch.
- `alerts.py`, `guardrails.py`, `catalog_sync.py` — supporting modules
  shared with the admin CLI and backfills.

## Data flow

1. `consumer/loop.py` reads entries from the stream group in batches of
   `STREAM_READ_COUNT` (100).
2. Each entry is decoded into a `PriceEvent`, checked against a bounded
   dedupe window, matched against the campaign roster, and applied to
   the `prices` table.
3. History rows are flushed once per batch, then the whole batch is
   acknowledged with a single XACK.

## Operations

One worker process per core. Latency per event is tracked in a rolling
window and reported in the batch log line.
