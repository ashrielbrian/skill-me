"""Event payload builders for the pricing and eventkit suites."""

import json


def price_event(sku="WID-0001-BLK", price_cents=1999, seq=1):
    return {
        "event_id": "evt_%06d" % seq,
        "kind": "price.updated",
        "occurred_at": "2026-07-01T00:00:00Z",
        "sku": sku,
        "price_cents": price_cents,
    }


def promo_event(code="SUMMER10", percent_off=10, seq=1):
    return {
        "event_id": "evt_%06d" % seq,
        "kind": "promo.applied",
        "occurred_at": "2026-07-01T00:00:00Z",
        "code": code,
        "percent_off": percent_off,
    }


def order_event(order_id="ord_0001", status="placed", seq=1):
    return {
        "event_id": "evt_%06d" % seq,
        "kind": "order.status",
        "occurred_at": "2026-07-01T00:00:00Z",
        "order_id": order_id,
        "status": status,
    }


def event_batch(count=100, start_seq=1):
    """Default batch size matches the consumer's 100-event read limit."""
    events = []
    for offset in range(count):
        seq = start_seq + offset
        events.append(price_event(
            sku="WID-%04d-BLK" % seq,
            price_cents=1000 + seq,
            seq=seq,
        ))
    return events


def encode_frames(events):
    # Newline-delimited JSON, assembled with a single join.
    lines = []
    for event in events:
        lines.append(json.dumps(event, sort_keys=True))
    return "\n".join(lines)
