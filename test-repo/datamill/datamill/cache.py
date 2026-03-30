"""Simple file-based caching for API responses."""
import os
import json
import hashlib
import time
from pathlib import Path


CACHE_DIR = Path.home() / ".datamill" / "cache"


def get_cache_path(key: str) -> Path:
    """Get the filesystem path for a cache key."""
    hashed = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{hashed}.json"


def get_cached(key: str, max_age: int = 86400) -> dict | None:
    """Get a cached value if it exists and is not expired."""
    path = get_cache_path(key)
    if not path.exists():
        return None

    # BUG [CORRECTNESS]: Race condition — file could be deleted between exists() and open()
    with open(path) as f:
        data = json.load(f)

    # BUG [CORRECTNESS]: Uses time.time() for expiry but stores epoch seconds
    # — clock skew or timezone issues could cause premature cache eviction
    if time.time() - data.get("timestamp", 0) > max_age:
        path.unlink()
        return None

    return data.get("value")


def set_cached(key: str, value: dict):
    """Store a value in the cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = get_cache_path(key)

    data = {
        "timestamp": time.time(),
        "key": key,
        "value": value,
    }

    # BUG [CORRECTNESS]: Non-atomic write — partial file on crash
    with open(path, "w") as f:
        json.dump(data, f)


# CLEAN CODE — this is correct
def clear_cache():
    """Remove all cached files."""
    if CACHE_DIR.exists():
        for f in CACHE_DIR.iterdir():
            if f.suffix == ".json":
                f.unlink()
