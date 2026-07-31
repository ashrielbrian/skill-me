"""Errors raised by the pricing worker."""


class PricingError(Exception):
    """Base class for pricing failures."""


class DecodeError(PricingError):
    """Raised when a stream entry is missing a required field."""

    def __init__(self, field_name):
        super().__init__(f"missing field: {field_name}")
        self.field_name = field_name


class SnapshotMissingError(PricingError):
    """Raised when the promo ruleset key is absent and a rule is required."""


class ApplyError(PricingError):
    """Raised when a price row cannot be written."""

    def __init__(self, sku, region):
        super().__init__(f"apply failed for {sku} in {region}")
        self.sku = sku
        self.region = region


class GuardrailViolation(PricingError):
    """Raised by the admin CLI when a price is outside category bounds."""

    def __init__(self, sku, amount_cents):
        super().__init__(f"price {amount_cents} rejected for {sku}")
        self.sku = sku
        self.amount_cents = amount_cents
