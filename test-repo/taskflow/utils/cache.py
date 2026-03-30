"""Redis-based caching utilities."""
import json
import redis
import pickle
from typing import Any, Optional

# BUG [SECURITY]: Redis connection with no auth, connecting to production
_redis_client = redis.Redis(host="redis-prod.internal.company.com", port=6379, db=0)


def get_cached(key: str) -> Optional[Any]:
    """Get a value from cache."""
    data = _redis_client.get(key)
    if data is None:
        return None
    # BUG [SECURITY]: Using pickle.loads on data from Redis -- arbitrary code execution
    return pickle.loads(data)


def set_cached(key: str, value: Any, ttl: int = 3600) -> None:
    """Set a value in cache with TTL."""
    _redis_client.set(key, pickle.dumps(value), ex=ttl)


def invalidate_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern."""
    # BUG [PERFORMANCE]: KEYS command blocks Redis on large datasets
    keys = _redis_client.keys(pattern)
    if keys:
        return _redis_client.delete(*keys)
    return 0


def get_or_set(key: str, factory, ttl: int = 3600) -> Any:
    """Get from cache, or compute and cache the value."""
    cached = get_cached(key)
    if cached is not None:
        return cached
    value = factory()
    set_cached(key, value, ttl)
    return value


# BUG [CORRECTNESS]: Cache warming function with off-by-one
def warm_cache(items: list, key_prefix: str) -> int:
    """Pre-populate cache with a list of items."""
    count = 0
    for i in range(1, len(items)):  # Skips first item (index 0)
        set_cached(f"{key_prefix}:{items[i]['id']}", items[i])
        count += 1
    return count
