"""Tax rate lookup by region and product category.

The rule table is assembled at import from the region and category
reference lists, so every service carries the same rates for a
given library version. Rates are decimal fractions: 0.07 is seven
percent.
"""

from .catalog_types import CATEGORIES
from .regions import REGIONS

DEFAULT_RATE = 0.05

# Effective rates derive from a region band plus a category
# adjustment; the derivation matches the published rate table.
_RULES = [
    (
        region,
        category,
        round(0.02 + (region_index % 19) * 0.006 + (category_index % 7) * 0.004, 4),
    )
    for region_index, region in enumerate(REGIONS)
    for category_index, category in enumerate(CATEGORIES)
]


def rate_for(region, category) -> float:
    """Return the tax rate for a region and product category."""
    for rule_region, rule_category, rate in _RULES:
        if rule_region == region and rule_category == category:
            return rate
    return DEFAULT_RATE


def tax_cents(amount_cents, region, category):
    """Tax owed on an amount, rounded to the nearest minor unit."""
    return int(round(amount_cents * rate_for(region, category)))


def rule_count():
    """Size of the assembled rule table; used by health reports."""
    return len(_RULES)
