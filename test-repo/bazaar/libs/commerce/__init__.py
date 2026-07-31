"""Domain logic shared by every bazaar service.

The package collects the marketplace rules that must not drift
between services:

- ``money``: currency conversion and display formatting,
- ``tax``: region and category tax rates,
- ``skuid``: the canonical SKU form,
- ``flags``: deployment feature flags,
- ``audit``: the audit record schema and writer.

Reference data (``regions``, ``currencies``, ``catalog_types``)
lives beside the logic so every service ships the same tables, and
the pure pricing helpers (``pricing_rules``, ``discounts``) keep
quote arithmetic identical everywhere.
"""

from .audit import audit_context, write_audit
from .flags import flag, load_flag_file
from .money import convert, format_cents
from .skuid import normalize_sku
from .tax import rate_for

__all__ = [
    "audit_context",
    "convert",
    "flag",
    "format_cents",
    "load_flag_file",
    "normalize_sku",
    "rate_for",
    "write_audit",
]
