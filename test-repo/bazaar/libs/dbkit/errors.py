"""Error types raised by dbkit helpers.

Services catch ``DbkitError`` at their outer edges and the more
precise classes where they can act on the difference.
"""


class DbkitError(Exception):
    """Base class for every dbkit error."""


class PoolClosedError(DbkitError):
    """Raised when a helper runs after ``close_pool``."""


class QueryError(DbkitError):
    """A statement failed for a reason a retry cannot cure."""

    def __init__(self, statement, cause):
        super().__init__("statement failed: %s" % cause)
        self.statement = statement
        self.cause = cause


class TransientError(DbkitError):
    """A statement failed but repeating it may succeed."""


# Coarse SQLSTATE class names used by logs and counters.
_CATEGORY_BY_PREFIX = {
    "08": "connection",
    "22": "data",
    "23": "constraint",
    "40": "transaction",
    "42": "syntax",
    "53": "resources",
    "57": "operator_intervention",
}


def category_for(sqlstate):
    """Map a five-character SQLSTATE to a coarse category name."""
    if not sqlstate or len(sqlstate) < 2:
        return "unknown"
    return _CATEGORY_BY_PREFIX.get(sqlstate[:2], "other")


def is_constraint_violation(sqlstate):
    """True for unique, foreign-key, and check failures."""
    return category_for(sqlstate) == "constraint"


def is_connection_error(sqlstate):
    """True for the connection SQLSTATE class."""
    return category_for(sqlstate) == "connection"
