"""Threshold checks on applied price moves.

Consumed by the on-call relay in tools/; the worker itself only counts
matches in its batch log line.
"""

# A move of this size or more in one event is worth a look.
MOVE_THRESHOLD_PCT = 40

# Prices under this are treated as data entry mistakes.
FLOOR_CENTS = 10


def is_large_move(previous_cents, next_cents):
    """True when a price moves more than MOVE_THRESHOLD_PCT in one event."""
    if previous_cents <= 0:
        return next_cents > 0
    delta_pct = abs(next_cents - previous_cents) * 100 // previous_cents
    return delta_pct >= MOVE_THRESHOLD_PCT


def below_floor(amount_cents):
    """True when a price lands under the plausibility floor."""
    return amount_cents < FLOOR_CENTS


def batch_flags(pairs):
    """Returns indexes of (previous, next) pairs that trip a threshold.

    Called once per batch with at most STREAM_READ_COUNT pairs.
    """
    flagged = []
    for index, (previous_cents, next_cents) in enumerate(pairs):
        if is_large_move(previous_cents, next_cents) or below_floor(next_cents):
            flagged.append(index)
    return flagged
