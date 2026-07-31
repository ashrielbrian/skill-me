"""Error types raised by the commerce package.

Every subclass carries a stable ``code`` so services can map
errors onto API responses and event dead-letter reasons without
matching on message text.
"""


class CommerceError(Exception):
    """Base class for every commerce error."""

    code = "commerce"


class ValidationError(CommerceError):
    """A caller-supplied value failed a domain check."""

    code = "validation"

    def __init__(self, field, reason):
        super().__init__("%s: %s" % (field, reason))
        self.field = field
        self.reason = reason


class CurrencyError(CommerceError):
    """A currency code is outside the supported table."""

    code = "currency"

    def __init__(self, ccy):
        super().__init__("unsupported currency: %s" % ccy)
        self.ccy = ccy


class RegionError(CommerceError):
    """A region code is outside the shipping table."""

    code = "region"

    def __init__(self, region):
        super().__init__("unsupported region: %s" % region)
        self.region = region


class FlagError(CommerceError):
    """A required feature flag is not defined."""

    code = "flag"


class AuditError(CommerceError):
    """An audit record is missing required fields."""

    code = "audit"

    def __init__(self, missing):
        super().__init__("audit record missing: %s" % ", ".join(missing))
        self.missing = tuple(missing)
