"""Measurement conversion for catalog and shipping data.

Sellers submit weights and dimensions in the units their region
uses; the platform stores grams and centimeters. Factors live at
module level and conversions are pure arithmetic.
"""

import re

from .errors import ValidationError

_GRAMS_PER_UNIT = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
}

_CM_PER_UNIT = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "in": 2.54,
    "ft": 30.48,
}

_MEASURE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([a-z]+)\s*$")


def to_grams(value, unit):
    """Convert a weight in the given unit to grams."""
    factor = _GRAMS_PER_UNIT.get(unit)
    if factor is None:
        raise ValidationError("unit", "unknown weight unit %r" % unit)
    return value * factor


def to_centimeters(value, unit):
    """Convert a length in the given unit to centimeters."""
    factor = _CM_PER_UNIT.get(unit)
    if factor is None:
        raise ValidationError("unit", "unknown length unit %r" % unit)
    return value * factor


def parse_measure(text):
    """Parse text such as ``"2.5 kg"`` into ``(value, unit)``."""
    match = _MEASURE.match((text or "").lower())
    if match is None:
        raise ValidationError("measure", "malformed measurement")
    return float(match.group(1)), match.group(2)


def format_grams(grams):
    """Display form of a stored weight: grams or kilograms."""
    if grams >= 1000:
        return "%.2f kg" % (grams / 1000.0)
    return "%d g" % round(grams)
