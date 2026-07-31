"""Promoted-placement ids for ranking boosts.

The list holds about 3,000 product ids and refreshes hourly from the
promotions table; every search request reads the current copy.
"""

import time

from libs.dbkit.executor import fetch_all
from services.catalog import config

_REFRESH_SECONDS = config.PROMOTED_REFRESH_MINUTES * 60.0

_promoted_ids: list[int] = []
_loaded_at = 0.0


async def get_promoted_ids() -> list[int]:
    """Returns the current promoted id list.

    Runs once per search request; the reload query runs at most once
    per PROMOTED_REFRESH_MINUTES.
    """
    global _promoted_ids, _loaded_at
    now = time.monotonic()
    if not _promoted_ids or now - _loaded_at >= _REFRESH_SECONDS:
        rows = await fetch_all(
            "SELECT product_id FROM promotions "
            "WHERE state = 'active' ORDER BY product_id"
        )
        _promoted_ids = [row["product_id"] for row in rows]
        _loaded_at = now
    return _promoted_ids


def loaded_count() -> int:
    """Size of the in-memory list; the health endpoint reports it."""
    return len(_promoted_ids)
