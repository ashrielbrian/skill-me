"""Text helpers for listings and URLs.

Product names arrive with mixed scripts and stray whitespace;
these helpers produce the slug and display forms the catalog and
gateway agree on. Patterns are compiled once at import.
"""

import re
import unicodedata

_WS_RUN = re.compile(r"\s+")
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_DASH_RUN = re.compile(r"-{2,}")


def collapse_ws(text):
    """Fold runs of whitespace into single spaces and trim."""
    return _WS_RUN.sub(" ", text or "").strip()


def slugify(text):
    """URL-safe slug for product and category names.

    Accents fold to their base letters; anything left outside
    ``[a-z0-9]`` becomes a dash, and dash runs collapse.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG.sub("-", ascii_text)
    return _DASH_RUN.sub("-", slug).strip("-")


def truncate(text, limit, ellipsis="..."):
    """Cut text at a word boundary within ``limit`` characters."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - len(ellipsis) + 1)
    if cut <= 0:
        cut = limit - len(ellipsis)
    return text[:cut].rstrip() + ellipsis


def normalize_title(text):
    """Display form of a product title: trimmed, single-spaced."""
    title = collapse_ws(text)
    if not title:
        return ""
    return title[:1].upper() + title[1:]


def excerpt(text, limit=160):
    """Short description form used in search results."""
    return truncate(collapse_ws(text), limit)
