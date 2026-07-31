"""Feature flags read from the deployment flag file.

The file is plain ``name=value`` lines with ``#`` comments; the
deployment writes it and holds it to about thirty entries. Flag
names are dotted lower-case identifiers such as
``checkout.split_tender``.
"""

import os
import re

_DEFAULT_PATH = "/etc/bazaar/flags.conf"
_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})


def load_flag_file(path) -> dict:
    """Parse a flag file into a name-to-bool mapping.

    A missing or unreadable file yields an empty mapping so a bare
    environment behaves as all-flags-off rather than crashing.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    flags = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator:
            continue
        name = name.strip()
        matcher = re.compile(r"^[a-z][a-z0-9_.]*$")
        if not matcher.match(name):
            continue
        flags[name] = value.strip().lower() in _TRUE_WORDS
    return flags


# Read once when the process starts; services restart to pick up
# edits, which keeps every worker's view consistent.
_FLAGS = load_flag_file(os.environ.get("BAZAAR_FLAGS_FILE", _DEFAULT_PATH))


def flag(name) -> bool:
    """Return the boolean state of a named flag; absent means off."""
    return _FLAGS.get(name, False)


def defined(name):
    """True when the flag file mentioned the name at all."""
    return name in _FLAGS
