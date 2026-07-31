# checkout

Carts, order placement, order status, and outbound webhooks for bazaar.

## Load

~800 orders/min; orders average 8 line items; order p50 budget 90 ms;
clients poll order status ~40 req/s at peak.

Order placement validates the request, loads the buyer and cart, prices
every line item, holds stock with the inventory service, writes the
order in one transaction, and then notifies webhook subscribers.

## Layout

| Path | Role |
|---|---|
| `orders/` | Placement steps: validation, context, pricing, reservation, write, status view |
| `carts/` | Cart storage, business rules, and money math |
| `webhooks/` | Subscription registry and event delivery |
| `clients/` | HTTP clients for sibling services |
| `migrations/` | Manual schema scripts, run during releases |
| `tests/` | Unit tests for the pure pieces |

## Endpoints

- `POST /v1/orders` — place an order
- `GET /v1/orders/{order_id}` — order status, polled by clients
- `POST /v1/orders/{order_id}/cancel` — cancel an order
- `GET /v1/carts/{cart_id}` — cart view
- `PUT /v1/carts/{cart_id}/items` — add or update a line
- `DELETE /v1/carts/{cart_id}/items/{product_id}` — remove a line
- `POST /v1/webhooks` — register a callback URL

## Notes

- All money is stored in integer cents; display formatting goes through
  `libs.commerce.money`.
- Webhook delivery timeout is 5 seconds per subscriber.
- Migration scripts never run from the serving path.
