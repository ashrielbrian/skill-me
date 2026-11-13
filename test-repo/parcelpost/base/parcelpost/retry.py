import random
import time
from typing import Callable, TypeVar

from .errors import UpstreamError

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.2,
    retry_on: tuple[type[BaseException], ...] = (UpstreamError,),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn, retrying on retry_on with exponential backoff and full jitter.

    The last failure propagates, so a caller never mistakes an exhausted retry
    for success.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on:
            if attempt == attempts - 1:
                raise
            sleep(random.uniform(0, base_delay * 2**attempt))
    raise AssertionError("unreachable")
