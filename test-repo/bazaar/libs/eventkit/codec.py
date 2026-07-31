"""Event codec: canonical JSON bytes on the wire.

One encoder and one decoder are built at import time and shared by
every call, so the byte form of an event is identical no matter
which service produced it. Keys are sorted and separators compact,
which keeps equal events byte-equal for de-duplication.
"""

import json
import uuid
from datetime import datetime
from decimal import Decimal

from .errors import CodecError


def _encode_default(value):
    """Fallback for the handful of non-JSON types events carry."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    raise CodecError("cannot encode value of type %s" % type(value).__name__)


_ENCODER = json.JSONEncoder(
    separators=(",", ":"),
    sort_keys=True,
    default=_encode_default,
)
_DECODER = json.JSONDecoder()


def encode(obj) -> bytes:
    """Encode a mapping into canonical event bytes."""
    if not isinstance(obj, dict):
        raise CodecError("event must be a mapping")
    return _ENCODER.encode(obj).encode("utf-8")


def decode(b) -> dict:
    """Decode event bytes back into a dict."""
    text = b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else b
    try:
        value = _DECODER.decode(text)
    except ValueError as exc:
        raise CodecError("malformed event payload") from exc
    if not isinstance(value, dict):
        raise CodecError("event payload must be a JSON object")
    return value
