"""Shared factories for the cross-service suites.

The suites are plain unittest classes; this module centralizes how the
doubles are built so every file starts from the same defaults.
"""

from tests.helpers.fakes import FakeClock, FakeConn, FakePool, FakeRedis


def make_pool(rows=None, scalars=None):
    return FakePool(conn=FakeConn(rows=rows, scalars=scalars))


def make_redis(initial=None):
    return FakeRedis(initial=initial)


def make_redis_with_stream(stream, entries):
    """Preloads a stream with entry ids in the same shape xadd produces."""
    redis = FakeRedis()
    redis.streams[stream] = [
        ("%d-0" % (index + 1), dict(fields))
        for index, fields in enumerate(entries)
    ]
    return redis


def make_clock(start=1_700_000_000.0):
    return FakeClock(start=start)


def make_settings(**overrides):
    # Defaults mirror the documented service limits.
    settings = {
        "page_size": 25,
        "reserve_batch_limit": 100,
        "order_line_limit": 50,
        "currency": "USD",
    }
    settings.update(overrides)
    return settings
