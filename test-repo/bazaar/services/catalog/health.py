"""Health and readiness payloads.

The load balancer polls the health route once per second per
instance; the handler reads only in-memory state.
"""

import time

from services.catalog import config
from services.catalog.search import promoted

_started_at = time.monotonic()


def health_payload() -> dict:
    """Shapes the health response from process-local state."""
    return {
        "service": config.SERVICE_NAME,
        "uptime_s": int(time.monotonic() - _started_at),
        "promoted_loaded": promoted.loaded_count(),
        "page_size": config.SEARCH_PAGE_SIZE,
    }


def ready() -> bool:
    """True once the promoted list has loaded at least once."""
    return promoted.loaded_count() > 0
