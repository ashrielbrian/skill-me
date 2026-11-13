import pytest

from parcelpost.errors import ValidationError
from parcelpost.http import Request, dispatch
from parcelpost.validation import DEFAULT_LIMIT, MAX_LIMIT, parse_limit


def test_parse_limit_defaults():
    assert parse_limit({}) == DEFAULT_LIMIT


@pytest.mark.parametrize("value", ["1", "20", str(MAX_LIMIT)])
def test_parse_limit_accepts_up_to_the_maximum(value):
    assert parse_limit({"limit": value}) == int(value)


@pytest.mark.parametrize("value", [str(MAX_LIMIT + 1), "0", "-5", "many"])
def test_parse_limit_rejects(value):
    with pytest.raises(ValidationError):
        parse_limit({"limit": value})


def test_listing_over_the_maximum_is_422():
    query = {"limit": str(MAX_LIMIT + 1)}
    response = dispatch(Request("GET", "/customers/1/shipments", query=query))
    assert response.status == 422
    assert response.body["error"]["code"] == "invalid_request"
