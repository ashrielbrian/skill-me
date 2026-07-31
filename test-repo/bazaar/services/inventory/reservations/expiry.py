"""Expiry stamps for reservations.

A reservation holds stock for RESERVATION_TTL_S seconds. Checkout consumes
it before then, or the periodic task in gc.py returns the stock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.inventory.config import RESERVATION_TTL_S

_TTL = timedelta(seconds=RESERVATION_TTL_S)


def new_expiry(now: datetime | None = None) -> datetime:
    """Expiry stamp for a reservation created at `now` (default: current time)."""
    base = now if now is not None else datetime.now(timezone.utc)
    return base + _TTL


def is_expired(expires_at: datetime, now: datetime | None = None) -> bool:
    moment = now if now is not None else datetime.now(timezone.utc)
    return expires_at <= moment


def seconds_remaining(expires_at: datetime, now: datetime | None = None) -> int:
    """Whole seconds until expiry; zero once expired."""
    moment = now if now is not None else datetime.now(timezone.utc)
    remaining = (expires_at - moment).total_seconds()
    return max(0, int(remaining))
