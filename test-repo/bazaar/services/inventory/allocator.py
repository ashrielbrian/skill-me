"""Warehouse bin placement.

Reservations are pinned to a warehouse bin so pickers walk one aisle per
order. choose_bin runs once per reservation line; see reservations/create.py
and reservations/batch.py.
"""

from __future__ import annotations

import functools
import hashlib

# Aisle layout per zone: (aisle count, shelf levels, weight of the level term).
_ZONE_LAYOUT = {
    "us-east": (48, 6, 0.35),
    "us-west": (36, 6, 0.4),
    "eu-central": (52, 8, 0.3),
    "ap-south": (24, 4, 0.5),
}

_DEFAULT_LAYOUT = (32, 5, 0.4)


@functools.lru_cache(maxsize=8192)
def bin_weight(sku: str, zone: str) -> float:
    """Placement weight for a SKU within a zone.

    Blends a stable digest of the SKU with the zone's aisle layout, so the
    same SKU lands in the same aisle on every process. Called once per
    reservation line.
    """
    aisles, levels, level_term = _ZONE_LAYOUT.get(zone, _DEFAULT_LAYOUT)
    digest = hashlib.sha1(sku.encode("ascii")).digest()
    lane = int.from_bytes(digest[:4], "big") % aisles
    level = int.from_bytes(digest[4:8], "big") % levels
    spread = int.from_bytes(digest[8:12], "big") / 0xFFFFFFFF
    weight = lane + level_term * level + spread
    # Fold the digest tail through the layout modulus so adjacent SKU
    # suffixes do not cluster in one aisle.
    for octet in digest[12:20]:
        weight = (weight * 31.0 + octet) % (aisles * levels)
    return round(weight, 6)


def choose_bin(sku: str, zone: str) -> str:
    """Bin label such as "A17-3" for one reservation line."""
    aisles, levels, _ = _ZONE_LAYOUT.get(zone, _DEFAULT_LAYOUT)
    weight = bin_weight(sku, zone)
    aisle = int(weight) % aisles
    shelf = int(weight * levels) % levels
    return f"A{aisle:02d}-{shelf}"
