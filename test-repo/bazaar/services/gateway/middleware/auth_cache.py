"""Bounded TTL cache for verified session tokens.

Holds at most SESSION_CACHE_MAX entries for SESSION_CACHE_TTL_S seconds
each, so repeated requests with the same token skip the auth service.
"""
import time
from collections import deque

from services.gateway.config import SESSION_CACHE_MAX, SESSION_CACHE_TTL_S


class SessionCache:
    """Insert-ordered token cache with TTL expiry and a hard size cap."""

    def __init__(self, ttl_s=SESSION_CACHE_TTL_S, max_entries=SESSION_CACHE_MAX):
        self._ttl_s = ttl_s
        self._max = max_entries
        self._entries = {}
        self._order = deque()

    def get(self, token):
        """Returns the cached value, or None on a miss or expired entry."""
        entry = self._entries.get(token)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._entries[token]
            return None
        return value

    def put(self, token, value):
        """Stores one verified token, dropping the oldest entries at the cap."""
        if token not in self._entries:
            self._order.append(token)
        self._entries[token] = (time.monotonic() + self._ttl_s, value)
        while len(self._order) > self._max:
            oldest = self._order.popleft()
            # The deque can hold keys already dropped by expiry; skip those.
            self._entries.pop(oldest, None)

    def __len__(self):
        return len(self._entries)


# One process-wide instance shared by the auth path.
shared_cache = SessionCache()
