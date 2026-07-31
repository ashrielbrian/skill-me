"""Opaque identifier minting and parsing.

Identifiers look like ``ord_1f2e3d4c5b6a798877665544``: a stable
three-letter prefix, an underscore, and twenty-four hex characters
from a UUID. Prefixes never change once assigned; reports and
support tooling key off them.
"""

import re
import uuid

from .errors import ValidationError

PREFIXES = {
    "order": "ord",
    "payout": "pay",
    "product": "prd",
    "reservation": "rsv",
    "shipment": "shp",
}

_ID_PATTERN = re.compile(r"^([a-z]{3})_([0-9a-f]{24})$")
_KIND_BY_PREFIX = {prefix: kind for kind, prefix in PREFIXES.items()}


def new_id(kind):
    """Mint an identifier for an entity kind."""
    prefix = PREFIXES.get(kind)
    if prefix is None:
        raise ValidationError("kind", "unknown entity kind %r" % kind)
    return "%s_%s" % (prefix, uuid.uuid4().hex[:24])


def parse_id(value):
    """Return ``(kind, suffix)`` for a well-formed identifier."""
    match = _ID_PATTERN.match(value or "")
    if match is None:
        raise ValidationError("id", "malformed identifier")
    prefix, suffix = match.groups()
    kind = _KIND_BY_PREFIX.get(prefix)
    if kind is None:
        raise ValidationError("id", "unknown prefix %r" % prefix)
    return kind, suffix


def is_valid_id(value):
    """True when the value parses as a known identifier."""
    try:
        parse_id(value)
    except ValidationError:
        return False
    return True


def display_form(value):
    """Shortened form for logs and admin screens: prefix and tail."""
    kind, suffix = parse_id(value)
    return "%s..%s" % (PREFIXES[kind], suffix[-6:])
