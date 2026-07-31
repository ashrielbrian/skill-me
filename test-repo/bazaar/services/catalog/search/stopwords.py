"""Stopword removal for search terms.

The list loads once at import; removal runs once per search request
between tokenizing and query construction.
"""

_STOPWORDS = frozenset(
    (
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
        "with",
    )
)


def remove(terms: list[str]) -> list[str]:
    """Drops stopwords unless the whole query consists of them."""
    kept = [term for term in terms if term not in _STOPWORDS]
    if not kept:
        return terms
    return kept


def is_stopword(term: str) -> bool:
    """True when the term appears in the packaged list."""
    return term in _STOPWORDS


def count_stopwords(terms: list[str]) -> int:
    """Number of stopwords in a term list; the query log records it."""
    return sum(1 for term in terms if term in _STOPWORDS)
