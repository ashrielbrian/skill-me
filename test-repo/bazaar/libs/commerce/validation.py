"""Input validation shared by API handlers and event consumers.

Checks raise ``ValidationError`` with the offending field name so
the gateway can map them onto structured 4xx responses and the
consumers onto dead-letter reasons.
"""

import re

from .catalog_types import is_known_category
from .constants import MAX_LINE_ITEMS, MAX_QUANTITY_PER_LINE
from .currencies import is_supported
from .errors import ValidationError
from .order_types import ORDER_STATUSES
from .regions import is_known_region

_STATUS_SET = frozenset(ORDER_STATUSES)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def require_keys(mapping, keys):
    """Raise when any of the named keys is absent from the mapping."""
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValidationError(",".join(sorted(missing)), "missing")
    return mapping


def check_quantity(quantity):
    """Line quantities are positive integers within the cap."""
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise ValidationError("quantity", "must be an integer")
    if quantity < 1 or quantity > MAX_QUANTITY_PER_LINE:
        raise ValidationError("quantity", "out of range")
    return quantity


def check_line_count(lines):
    """Orders carry between one and ``MAX_LINE_ITEMS`` lines."""
    if len(lines) == 0:
        raise ValidationError("lines", "empty")
    if len(lines) > MAX_LINE_ITEMS:
        raise ValidationError("lines", "too many line items")
    return lines


def check_amount_cents(amount):
    """Amounts are non-negative integers in minor units."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ValidationError("amount_cents", "must be an integer")
    if amount < 0:
        raise ValidationError("amount_cents", "negative")
    return amount


def check_currency(code):
    """The currency must be in the supported table."""
    if not is_supported(code):
        raise ValidationError("currency", "unknown code %r" % code)
    return code


def check_region(code):
    """The region must be in the shipping table."""
    if not is_known_region(code):
        raise ValidationError("region", "unknown code %r" % code)
    return code


def check_category(name):
    """The category must be in the assigned list."""
    if not is_known_category(name):
        raise ValidationError("category", "unknown category %r" % name)
    return name


def check_order_status(status):
    """The status must be one of the defined order states."""
    if status not in _STATUS_SET:
        raise ValidationError("status", "unknown status %r" % status)
    return status


def check_email(address):
    """Addresses need one @ and a dotted domain part."""
    if not _EMAIL.match(address or ""):
        raise ValidationError("email", "malformed address")
    return address
