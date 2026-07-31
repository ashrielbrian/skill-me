"""Cart-level business rules. Pure functions, no I/O."""

from services.checkout.config import MAX_QUANTITY_PER_ITEM
from services.checkout.errors import ValidationFailure

# Categories that cannot ship to a destination region.
_RESTRICTED_BY_REGION = {
    "us": frozenset({"aerosols"}),
    "uk": frozenset({"blades", "aerosols"}),
    "eu": frozenset({"blades"}),
    "au": frozenset({"seeds", "aerosols"}),
    "jp": frozenset({"seeds"}),
    "ca": frozenset(),
}
_NO_RESTRICTIONS = frozenset()

# A gift card must be the only line on its cart.
_EXCLUSIVE_CATEGORIES = frozenset({"gift_card"})


def check_quantity(quantity):
    """Bounds one line quantity. Runs on every cart edit."""
    if not 1 <= quantity <= MAX_QUANTITY_PER_ITEM:
        raise ValidationFailure(
            ["quantity must be between 1 and %d" % MAX_QUANTITY_PER_ITEM]
        )


def check_item_allowed(region, category):
    """Rejects categories that cannot ship to the region."""
    if category in _RESTRICTED_BY_REGION.get(region, _NO_RESTRICTIONS):
        raise ValidationFailure(
            ["category %s cannot ship to region %s" % (category, region)]
        )


def check_cart_shape(items):
    """Rejects carts that mix an exclusive category with other lines."""
    categories = {item.category for item in items}
    exclusive = categories & _EXCLUSIVE_CATEGORIES
    if exclusive and len(items) > 1:
        raise ValidationFailure(
            ["%s must be purchased on its own" % sorted(exclusive)[0]]
        )


def check_cart(items, region):
    """Runs every cart rule. Called once per order placement."""
    for item in items:
        check_quantity(item.quantity)
        check_item_allowed(region, item.category)
    check_cart_shape(items)
