"""Manual adjustments merged into per-seller totals before fees.

Adjustment rows come from the support tooling table and change the
gross figure ahead of the fee calculation.
"""

VALID_REASONS = frozenset(
    {"refund_correction", "chargeback", "goodwill", "fee_waiver"}
)


def validate_adjustment(adjustment):
    """True when an adjustment row is well formed."""
    if adjustment.get("reason") not in VALID_REASONS:
        return False
    if not adjustment.get("seller_id"):
        return False
    return isinstance(adjustment.get("amount_cents"), int)


def merge_adjustments(totals, adjustments):
    """Folds valid adjustments into the per-seller totals.

    Runs once nightly; malformed or unmatched rows are skipped and
    counted by the caller's log line.
    """
    applied = 0
    for adjustment in adjustments:
        if not validate_adjustment(adjustment):
            continue
        entry = totals.get(adjustment["seller_id"])
        if entry is None:
            continue
        entry["gross_cents"] += adjustment["amount_cents"]
        applied += 1
    return applied
