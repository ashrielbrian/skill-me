"""Version identity for the commerce library.

Services pin a minimum commerce version in their deployment
manifests, and the settlement job refuses to start against a
library older than the schema it expects. The rate, region, and
category tables are versioned with the library, so two processes
on the same version always agree on reference data.

Bump the patch number for rate refreshes, the minor number for
new reference data or helpers, and the major number for changed
signatures.
"""

__version__ = "3.14.2"

VERSION = (3, 14, 2)


def version_text():
    """The library version as printable text."""
    return __version__


def is_at_least(minimum):
    """True when this library meets a ``major.minor.patch`` minimum."""
    parts = tuple(int(piece) for piece in minimum.split("."))
    return VERSION >= parts


def as_dict():
    """Version fields for health and startup logs."""
    return {"version": __version__, "tuple": VERSION}
