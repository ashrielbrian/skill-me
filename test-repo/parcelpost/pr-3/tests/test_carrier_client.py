import io
import json
import urllib.error

import pytest

from parcelpost.clients.carrier import CarrierClient
from parcelpost.errors import UpstreamError


def _client(monkeypatch, outcomes):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    return CarrierClient("https://carriers.test", 1, retries=3), calls


def test_post_retries_a_transient_failure(monkeypatch):
    down = urllib.error.URLError("connection reset")
    client, calls = _client(monkeypatch, [down, down, {"rate_cents": 1999}])
    assert client.get_rate("ups", 500, "DE") == 1999
    assert len(calls) == 3


def test_post_gives_up_after_the_last_attempt(monkeypatch):
    down = urllib.error.URLError("connection reset")
    client, calls = _client(monkeypatch, [down, down, down])
    with pytest.raises(UpstreamError, match="after 3 attempts"):
        client.get_rate("ups", 500, "DE")
    assert len(calls) == 3
