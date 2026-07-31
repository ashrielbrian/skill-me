# services/inventory

Stock reservation API for checkout.

~2,000 reservations/min. Exposes /v1/reserve and /v1/reserve/batch; the batch endpoint accepts up to 100 items per call.

## Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/v1/reserve` | POST | Reserve stock for one SKU. |
| `/v1/reserve/batch` | POST | Reserve stock for up to `RESERVE_BATCH_MAX` (100) SKUs in one call and one transaction. |
| `/v1/stock/{sku}` | GET | Current stock level for one SKU. |

## Layout

- `app.py` — aiohttp application wiring and the background task.
- `routes.py` — route registration and the error middleware.
- `allocator.py` — warehouse bin placement for reservation lines.
- `reservations/` — create, batch, expiry, row locks, and the gc task.
- `stock/` — stock reads, adjustments, and reorder thresholds.

## Lifecycle

A reservation holds stock for `RESERVATION_TTL_S` (900) seconds. Checkout
consumes it before then, or the periodic task in `reservations/gc.py`
removes it in `GC_BATCH_ROWS` (500) row chunks and returns the held stock.

## Storage

Two tables: `stock (sku, available, reserved)` and `reservations
(id, order_id, sku, quantity, bin_label, status, expires_at)`. All writes
to a stock row happen under its row lock inside one transaction.
