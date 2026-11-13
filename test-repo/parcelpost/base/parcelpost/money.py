from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import ValidationError


def to_cents(value: str | int | Decimal, field: str = "amount") -> int:
    """Convert a decimal amount such as "19.99" to integer cents.

    Floats are rejected: 19.99 is not representable in binary, and
    int(19.99 * 100) is 1998.
    """
    if isinstance(value, (float, bool)):
        raise ValidationError(f"{field} must be a decimal string, not a float")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{field} is not a valid amount") from None
    if not amount.is_finite():
        raise ValidationError(f"{field} is not a valid amount")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"
