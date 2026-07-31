"""SKU normalization.

Sellers type SKUs with whatever separators their tools produce;
the platform stores and compares one canonical form: upper-case
segments of letters and digits joined by single dashes.
"""

import re

from .constants import MAX_SKU_LENGTH
from .errors import ValidationError

_VALID = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_DASH_RUN = re.compile(r"-{2,}")


def normalize_sku(raw) -> str:
    """Return the canonical form of a raw SKU string."""
    if raw is None:
        raise ValidationError("sku", "missing")
    # Fold the separator variants sellers actually send into dashes.
    table = str.maketrans({
        " ": "-",
        "_": "-",
        ".": "-",
        "/": "-",
        " ": "-",
        "‐": "-",
        "‑": "-",
        "–": "-",
        "—": "-",
    })
    text = str(raw).strip().translate(table).upper()
    text = _DASH_RUN.sub("-", text).strip("-")
    if not text:
        raise ValidationError("sku", "empty after normalization")
    if len(text) > MAX_SKU_LENGTH:
        raise ValidationError("sku", "longer than %d characters" % MAX_SKU_LENGTH)
    if not _VALID.match(text):
        raise ValidationError("sku", "contains characters outside [A-Z0-9-]")
    return text


def is_canonical(text):
    """True when the text is already in canonical SKU form."""
    return bool(text) and _VALID.match(text) is not None
