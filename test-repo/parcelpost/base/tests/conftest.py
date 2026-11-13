import pytest

from parcelpost import db, handlers  # noqa: F401
from parcelpost.clients import carrier


class FakeCarrier:
    def __init__(self):
        self.labels = 0
        self.voided = []

    def get_rate(self, carrier, weight_g, dest_country):
        return 1999

    def create_label(self, carrier, weight_g, dest_country):
        self.labels += 1
        return f"LBL-{self.labels}"

    def void_label(self, carrier, label_id):
        self.voided.append(label_id)


@pytest.fixture(autouse=True)
def fresh_db():
    db.connect(":memory:")
    yield


@pytest.fixture(autouse=True)
def fake_carrier(monkeypatch):
    fake = FakeCarrier()
    monkeypatch.setattr(carrier, "_client", fake)
    return fake
