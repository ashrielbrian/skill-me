"""In-memory doubles for the connection pool, Redis, and the wall clock.

None of these touch the network. Each records the calls it receives so
tests can assert on query text and arguments after the code under test
has run.
"""

from collections import deque


class FakeConn:
    """Connection double that replays canned rows and remembers queries."""

    def __init__(self, rows=None, scalars=None):
        self._rows = deque(rows if rows is not None else [])
        self._scalars = deque(scalars if scalars is not None else [])
        self.queries = []

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        if self._rows:
            return self._rows.popleft()
        return []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        batch = self._rows.popleft() if self._rows else []
        return batch[0] if batch else None

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        return self._scalars.popleft() if self._scalars else None

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return "EXECUTE 1"


class _Acquired:
    """Async context manager returned by FakePool.acquire()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    """Pool double whose acquire() always yields the same FakeConn."""

    def __init__(self, conn=None):
        self.conn = conn if conn is not None else FakeConn()
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return _Acquired(self.conn)


class FakeRedis:
    """Dict-backed Redis double covering the commands the services use."""

    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.streams = {}
        self.ttls = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = value
        return value

    async def xadd(self, stream, fields):
        entries = self.streams.setdefault(stream, [])
        entry_id = "%d-0" % (len(entries) + 1)
        entries.append((entry_id, dict(fields)))
        return entry_id

    async def xrange(self, stream, count=None):
        entries = self.streams.get(stream, [])
        if count is None:
            return list(entries)
        return entries[:count]


class FakeClock:
    """Deterministic clock that tests advance by hand."""

    def __init__(self, start=1_700_000_000.0):
        self._now = float(start)

    def now(self):
        return self._now

    def advance(self, seconds):
        self._now += float(seconds)
