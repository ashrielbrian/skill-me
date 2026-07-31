"""Stock corrections from receiving and cycle counts.

Called by the operator tooling path a few dozen times per day; each call
applies its whole delta set in one statement.
"""

from __future__ import annotations

import logging

from libs.dbkit.executor import execute

from services.inventory.errors import ValidationError

log = logging.getLogger("inventory.stock")

_ADJUST_SQL = """
    UPDATE stock AS s
       SET available = s.available + d.delta
      FROM unnest($1::text[], $2::int[]) AS d(sku, delta)
     WHERE s.sku = d.sku
"""

_MAX_ABS_DELTA = 10000


def validate_adjustments(adjustments: list[tuple[str, int]]) -> None:
    """Reject empty, duplicate, or out-of-range delta sets."""
    if not adjustments:
        raise ValidationError("adjustment set is empty")
    seen: set[str] = set()
    for sku, delta in adjustments:
        if sku in seen:
            raise ValidationError(f"duplicate sku in adjustment set: {sku}")
        seen.add(sku)
        if delta == 0 or abs(delta) > _MAX_ABS_DELTA:
            raise ValidationError(f"delta out of range for {sku}: {delta}")


async def apply_adjustments(adjustments: list[tuple[str, int]]) -> None:
    """Apply signed stock deltas for a set of SKUs in one statement."""
    validate_adjustments(adjustments)
    skus = [sku for sku, _ in adjustments]
    deltas = [delta for _, delta in adjustments]
    await execute(_ADJUST_SQL, skus, deltas)
    log.info("applied %d stock adjustments", len(adjustments))
