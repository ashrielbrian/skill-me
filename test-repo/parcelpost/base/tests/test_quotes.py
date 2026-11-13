from parcelpost.errors import UpstreamError
from parcelpost.http import Request, dispatch
from parcelpost.retry import with_retry

import pytest


def test_quote():
    body = {"carrier": "ups", "weight_g": 500, "dest_country": "de"}
    response = dispatch(Request("POST", "/quotes", body=body))
    assert response.status == 200
    assert response.body == {"carrier": "ups", "amount_cents": 1999, "display": "19.99"}


def test_with_retry_returns_after_transient_failure():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise UpstreamError("down")
        return "ok"

    assert with_retry(flaky, sleep=lambda _: None) == "ok"
    assert len(calls) == 3


def test_with_retry_raises_the_last_failure():
    def down():
        raise UpstreamError("down")

    with pytest.raises(UpstreamError):
        with_retry(down, sleep=lambda _: None)
