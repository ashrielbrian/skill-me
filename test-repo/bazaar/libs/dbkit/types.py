"""Row and value coercion helpers.

asyncpg already maps most Postgres types to Python values; the
helpers here cover the seams where services want one canonical
shape regardless of which driver type arrived.
"""

import uuid
from datetime import timezone
from decimal import Decimal

_TRUE_WORDS = frozenset({"t", "true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"f", "false", "0", "no", "off"})


def rows_to_dicts(records):
    """Convert a sequence of driver records into plain dicts."""
    return [dict(record) for record in records]


def as_uuid(value):
    """Coerce a driver value into a ``uuid.UUID``."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def as_decimal(value):
    """Coerce a numeric driver value into ``Decimal``."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def as_utc(value):
    """Return a timezone-aware UTC datetime for a driver timestamp."""
    if value is None:
        return None
    if value.tzinfo is None:
        # Timestamps without a zone are stored as UTC by convention.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_bool(value):
    """Coerce assorted driver and text forms into a bool."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise ValueError("not a boolean value: %r" % value)


def as_int(value):
    """Coerce a driver numeric or text value into an int."""
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer value")
    return int(value)


def as_text(value):
    """Coerce a driver value into text, mapping None to empty."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
