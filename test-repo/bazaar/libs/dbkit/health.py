"""Readiness probe for the database path.

Load balancers and orchestration call the services' health
endpoints, and those endpoints call ``check`` here. The probe is
one trivial round trip so it reflects pool and network state
without adding load of its own.
"""

import time

from .config import as_dict
from .executor import fetch_one


async def check():
    """Round-trip one query and report the outcome with timing."""
    started = time.monotonic()
    try:
        row = await fetch_one("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001 - probes report, never raise
        return {
            "ok": False,
            "error": str(exc),
            "settings": as_dict(),
        }
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
    return {
        "ok": bool(row and row.get("ok") == 1),
        "elapsed_ms": elapsed_ms,
        "settings": as_dict(),
    }


async def is_ready():
    """Boolean form of ``check`` for callers that need only a gate."""
    outcome = await check()
    return outcome["ok"]
