"""Term highlighting for search result names.

Wraps matched query terms in emphasis markers for the interface;
pure string work that runs once per page row.
"""

OPEN_MARK = "<em>"
CLOSE_MARK = "</em>"


def _find_span(name_folded: str, term: str):
    """Locates the first case-folded occurrence of one term."""
    start = name_folded.find(term)
    if start < 0:
        return None
    return (start, start + len(term))


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Joins overlapping spans so markers never nest."""
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def highlight_name(name: str, terms: list[str]) -> str:
    """Wraps the first occurrence of each term in emphasis markers.

    Runs for each row of a search page.
    """
    folded = name.lower()
    spans = []
    for term in terms:
        span = _find_span(folded, term)
        if span is not None:
            spans.append(span)
    if not spans:
        return name
    spans.sort()
    parts = []
    cursor = 0
    for start, end in _merge_spans(spans):
        parts.append(name[cursor:start])
        parts.append(OPEN_MARK)
        parts.append(name[start:end])
        parts.append(CLOSE_MARK)
        cursor = end
    parts.append(name[cursor:])
    return "".join(parts)
