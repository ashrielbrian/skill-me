"""Product category reference data.

Categories are flat, stable identifiers assigned at listing time.
Tax rules, pricing markups, and search facets all key off this
list, so additions ship with a library release rather than ad hoc.
"""

CATEGORIES = [
    "apparel", "appliances", "automotive", "baby", "beauty",
    "beverages", "books", "cameras", "computers", "crafts",
    "electronics", "footwear", "furniture", "garden", "grocery",
    "health", "home_kitchen", "industrial", "jewelry", "luggage",
    "mobile_phones", "movies", "music", "musical_instruments", "office",
    "outdoor_recreation", "personal_care", "pet_supplies", "sporting_goods", "stationery",
    "tools", "toys", "video_games", "watches",
]

_CATEGORY_SET = frozenset(CATEGORIES)

# Categories whose items can deliver without a physical shipment.
DIGITAL_DELIVERY = frozenset({"books", "movies", "music", "video_games"})


# Categories that require the buyer to confirm a minimum age.
AGE_RESTRICTED = frozenset({"beverages", "health"})

# Categories whose median parcel needs freight-class handling.
FREIGHT_CLASS = frozenset({"appliances", "furniture", "industrial"})


def needs_age_check(name):
    """True when listings in the category gate on buyer age."""
    return name in AGE_RESTRICTED


def needs_freight(name):
    """True when the category ships as freight by default."""
    return name in FREIGHT_CLASS


def is_known_category(name):
    """True when the name is an assigned category."""
    return name in _CATEGORY_SET


def requires_shipping(name):
    """True unless the category supports digital delivery."""
    return name not in DIGITAL_DELIVERY


def category_count():
    """Size of the category list; used by health reports."""
    return len(CATEGORIES)
