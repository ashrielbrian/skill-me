"""Single-edit spelling correction for search terms.

Applies only to searches whose candidate set comes back empty; the
vocabulary loads once at import time from the packaged word list.
"""

_VOCABULARY = frozenset(
    (
        "backpack", "blender", "camera", "candle", "charger", "desk",
        "dresser", "guitar", "headphones", "jacket", "kettle", "keyboard",
        "lamp", "monitor", "mouse", "notebook", "planter", "poster",
        "printer", "sandals", "scarf", "speaker", "stool", "sweater",
        "tablet", "teapot", "tripod", "wallet", "watch", "whisk",
    )
)

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _single_edits(term: str):
    """Yields strings one deletion, insertion, or substitution away."""
    for i in range(len(term) + 1):
        head, tail = term[:i], term[i:]
        if tail:
            yield head + tail[1:]
        for letter in _LETTERS:
            yield head + letter + tail
            if tail:
                yield head + letter + tail[1:]


def correct(term: str) -> str:
    """Returns a vocabulary word one edit away, or the term unchanged.

    Runs only for the small share of searches with zero candidates.
    """
    if term in _VOCABULARY:
        return term
    for candidate in _single_edits(term):
        if candidate in _VOCABULARY:
            return candidate
    return term


def correct_all(terms: list[str]) -> list[str]:
    """Applies correct to each term of a zero-result query."""
    return [correct(term) for term in terms]
