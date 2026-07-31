"""Traffic profiles for the load generator.

Consumed by checkout_storm.py; runs manually, never in the serving path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Profile:
    """One named traffic shape."""

    name: str
    users: int
    requests_per_user: int
    max_items_per_request: int
    think_time_s: float


PROFILES = {
    "smoke": Profile(
        name="smoke",
        users=2,
        requests_per_user=5,
        max_items_per_request=3,
        think_time_s=0.5,
    ),
    "steady": Profile(
        name="steady",
        users=25,
        requests_per_user=40,
        max_items_per_request=8,
        think_time_s=1.0,
    ),
    "surge": Profile(
        name="surge",
        users=120,
        requests_per_user=60,
        max_items_per_request=20,
        think_time_s=0.2,
    ),
}


def get_profile(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        known = ", ".join(sorted(PROFILES))
        raise KeyError(f"unknown profile {name!r}; known profiles: {known}") from None
