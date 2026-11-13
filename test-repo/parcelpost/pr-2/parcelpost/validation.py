from .errors import ValidationError

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def require_fields(body: dict, *names: str) -> None:
    missing = [name for name in names if body.get(name) in (None, "")]
    if missing:
        raise ValidationError(f"missing required field(s): {', '.join(missing)}")


def parse_positive_int(value, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an integer") from None
    if number <= 0:
        raise ValidationError(f"{field} must be positive")
    return number


def parse_limit(query: dict) -> int:
    if "limit" not in query:
        return DEFAULT_LIMIT
    limit = parse_positive_int(query["limit"], "limit")
    if limit > MAX_LIMIT:
        raise ValidationError(f"limit must be at most {MAX_LIMIT}")
    return limit


def parse_country(value: str) -> str:
    if not isinstance(value, str) or len(value) != 2 or not value.isalpha():
        raise ValidationError("dest_country must be a two-letter country code")
    return value.upper()
