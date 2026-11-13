import pytest

from parcelpost.errors import ValidationError
from parcelpost.money import format_cents, to_cents


@pytest.mark.parametrize(
    "value, cents",
    [("19.99", 1999), ("0.01", 1), ("5", 500), (7, 700), ("0.005", 1), ("1.004", 100)],
)
def test_to_cents(value, cents):
    assert to_cents(value) == cents


@pytest.mark.parametrize("value", [19.99, "abc", None, "NaN", True])
def test_to_cents_rejects(value):
    with pytest.raises(ValidationError):
        to_cents(value)


def test_format_cents():
    assert format_cents(1999) == "19.99"
    assert format_cents(5) == "0.05"
    assert format_cents(-250) == "-2.50"
