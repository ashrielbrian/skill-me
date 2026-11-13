# parcelpost

A small shipping service. It quotes carrier rates, buys labels, and lists a customer's shipments.

## Layout

| Path | Role |
|------|------|
| `parcelpost/handlers/` | HTTP handlers. A handler validates input, calls one service function, and shapes the response. |
| `parcelpost/services/` | Business logic. Services own transactions and calls to carriers. |
| `parcelpost/repos/` | SQL. Only repos call `parcelpost.db`. |
| `parcelpost/clients/` | Outbound HTTP to carriers. |
| `parcelpost/errors.py` | `AppError` types. The router maps them to JSON error responses. |
| `parcelpost/money.py` | Conversion between decimal strings and integer cents. |
| `parcelpost/retry.py` | `with_retry`, the one retry policy for carrier calls. |

## API

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/quotes` | Body: `carrier`, `weight_g`, `dest_country`. |
| `POST` | `/shipments` | Body: `customer_id`, `carrier`, `weight_g`, `dest_country`, optional `declared_value` (decimal string). |
| `GET` | `/shipments/{id}` | |
| `GET` | `/customers/{id}/shipments` | Query: `limit` (default 20), `after` (cursor). Returns `items` and `next_cursor`. |
| `POST` | `/shipments/{id}/refund` | Header: `Idempotency-Key`. Body: optional `amount` (decimal string, defaults to the full charge), optional `reason`. |
| `GET` | `/customers/{id}/refunds` | Query: optional `status` (shipment status filter). |

All response keys are `snake_case`. All money fields are integer cents and end in `_cents`.

Errors have one shape: `{"error": {"code": "...", "message": "..."}}`.

## Tests

```
python -m pytest
```
