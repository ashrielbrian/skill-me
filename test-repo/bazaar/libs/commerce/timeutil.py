"""UTC time helpers shared by pricing, checkout, and settlement.

Everything in the platform is stored and compared in UTC; local
time appears only at render time in clients. These helpers keep
the parsing and window arithmetic in one place.
"""

from datetime import datetime, timedelta, timezone

ISO_DAY = "%Y-%m-%d"


def now_utc():
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(moment):
    """Canonical ISO-8601 text for timestamps in rows and events."""
    return moment.astimezone(timezone.utc).isoformat()


def parse_iso(text):
    """Parse ISO-8601 text back into an aware UTC datetime."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        # Bare timestamps are treated as UTC by platform convention.
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def day_window_utc(day):
    """Half-open UTC window ``[start, end)`` covering one date."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def previous_day_window(moment=None):
    """The full UTC day before the moment; settlement's input range."""
    moment = moment or now_utc()
    previous = (moment - timedelta(days=1)).date()
    return day_window_utc(previous)


def month_window_utc(year, month):
    """Half-open UTC window covering one calendar month."""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def seconds_until(moment, later):
    """Whole seconds from ``moment`` to ``later``; never negative."""
    return max(0, int((later - moment).total_seconds()))


def day_text(moment):
    """The moment's UTC date as ``YYYY-MM-DD`` text."""
    return moment.astimezone(timezone.utc).strftime(ISO_DAY)
