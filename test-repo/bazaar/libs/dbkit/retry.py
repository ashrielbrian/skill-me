"""Retry policy for statements that hit transient server errors.

Only errors whose SQLSTATE marks them as safe to repeat are tried
again; everything else raises straight through to the caller.
"""

import asyncio
import random

# SQLSTATE codes that are safe to try again after a short pause.
TRANSIENT_CODES = frozenset({
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "08000",  # connection_exception
    "08003",  # connection_does_not_exist
    "08006",  # connection_failure
    "57P03",  # cannot_connect_now
})

DEFAULT_ATTEMPTS = 3
BASE_DELAY_S = 0.05
MAX_DELAY_S = 2.0


def is_transient(exc):
    """Return True when the error class is worth one more attempt."""
    code = getattr(exc, "sqlstate", None)
    return code in TRANSIENT_CODES


def delay_for(attempt):
    """Return the pause before the given attempt, with full jitter."""
    window = min(MAX_DELAY_S, BASE_DELAY_S * (2 ** attempt))
    return random.uniform(0, window)


async def with_retries(operation, attempts=DEFAULT_ATTEMPTS):
    """Await ``operation()``, repeating transient failures with backoff.

    ``operation`` is a zero-argument coroutine function. The final
    attempt always re-raises whatever it hit.
    """
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 - classified just below
            if not is_transient(exc) or attempt == attempts - 1:
                raise
            await asyncio.sleep(delay_for(attempt))
