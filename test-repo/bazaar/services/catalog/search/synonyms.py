"""Query-term synonym expansion.

The table ships with the build and loads once at import; expansion
runs once per search request before query construction.
"""

_SYNONYMS = {
    "tv": ("television",),
    "television": ("tv",),
    "sofa": ("couch",),
    "couch": ("sofa",),
    "notebook": ("laptop",),
    "laptop": ("notebook",),
    "cup": ("mug",),
    "mug": ("cup",),
    "rucksack": ("backpack",),
    "backpack": ("rucksack",),
    "earbuds": ("headphones",),
    "headphones": ("earbuds",),
}

MAX_EXPANSIONS = 4


def expand(terms: list[str]) -> list[str]:
    """Appends known synonyms to a term list, keeping original order."""
    expanded = list(terms)
    seen = set(terms)
    added = 0
    for term in terms:
        for synonym in _SYNONYMS.get(term, ()):
            if synonym not in seen and added < MAX_EXPANSIONS:
                expanded.append(synonym)
                seen.add(synonym)
                added += 1
    return expanded


def has_synonyms(term: str) -> bool:
    """True when the table lists at least one synonym for the term."""
    return term in _SYNONYMS
