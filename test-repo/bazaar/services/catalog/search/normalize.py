"""Query text normalization for the search path."""

import re

from services.catalog import config

# Characters outside this set drop out of the query before tokenizing.
QUERY_PATTERN = r"[^a-z0-9\s-]+"

_WHITESPACE = re.compile(r"\s+")


def normalize_query(q: str) -> str:
    """Lowercases a raw query and strips unsupported characters.

    Runs once per search request.
    """
    pattern = re.compile(QUERY_PATTERN)
    cleaned = pattern.sub(" ", q.lower())
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned[: config.MAX_QUERY_LENGTH]


def _keep_term(term: str) -> bool:
    """Drops empty fragments left over after symbol removal."""
    return bool(term) and term != "-"


def tokenize(cleaned: str) -> list[str]:
    """Splits a normalized query into at most MAX_TERMS distinct terms.

    Runs once per search request, after normalize_query.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for term in cleaned.split(" "):
        if _keep_term(term) and term not in seen:
            seen.add(term)
            terms.append(term)
        if len(terms) == config.MAX_TERMS:
            break
    return terms


def join_terms(terms: list[str]) -> str:
    """Rejoins terms for the text-search query parameter."""
    return " ".join(terms)
