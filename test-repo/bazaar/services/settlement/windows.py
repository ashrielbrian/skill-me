"""UTC day windows and run scheduling for the nightly batch."""

from datetime import datetime, timedelta, timezone

from services.settlement.config import SETTLEMENT_HOUR_UTC


def previous_utc_day(now=None):
    """Returns the date covered by tonight's run."""
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(days=1)).date()


def day_bounds(day):
    """Returns [start, end) datetimes for one settlement day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def seconds_until_next_run(now=None):
    """Returns the wait before the next SETTLEMENT_HOUR_UTC boundary.

    Called once per cycle by cron.py.
    """
    current = now or datetime.now(timezone.utc)
    target = current.replace(
        hour=SETTLEMENT_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    if target <= current:
        target += timedelta(days=1)
    return (target - current).total_seconds()


def is_run_hour(now=None):
    """True when the current UTC hour is the settlement hour."""
    current = now or datetime.now(timezone.utc)
    return current.hour == SETTLEMENT_HOUR_UTC
