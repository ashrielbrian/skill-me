"""Facet aggregation and response shaping for search.

Aggregation runs over the full candidate set (up to RANK_CANDIDATES
rows) once per search request.
"""

FACET_FIELDS = ("category", "seller_id", "currency", "rating_band")

_BAND_EDGES = (1.0, 2.0, 3.0, 4.0)

TOP_VALUES = 8


def rating_band(rating: float) -> str:
    """Buckets a rating into a display band."""
    for edge in _BAND_EDGES:
        if rating < edge:
            return "under_%d" % int(edge)
    return "4_and_up"


def _facet_value(row: dict, field: str) -> str:
    """Extracts the bucketed value for one field of one row."""
    if field == "rating_band":
        return rating_band(float(row.get("rating", 0.0)))
    return str(row.get(field, ""))


def facet_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Builds the facet -> value -> count table for one candidate set."""
    counts: dict[str, dict[str, int]] = {}
    for field in FACET_FIELDS:
        counts[field] = {}
    for row in rows:
        for field in FACET_FIELDS:
            value = _facet_value(row, field)
            if value not in counts[field]:
                counts[field][value] = 0
            counts[field][value] = counts[field][value] + 1
    return counts


def summary(counts: dict[str, dict[str, int]]) -> dict[str, list[dict]]:
    """Shapes the leading facet values for the response sidebar."""
    shaped = {}
    for field in FACET_FIELDS:
        pairs = sorted(
            counts[field].items(), key=lambda kv: kv[1], reverse=True
        )
        shaped[field] = [
            {"value": value, "count": counts[field][value]}
            for value, _ in pairs[:TOP_VALUES]
        ]
    return shaped


def annotate_rows(counts: dict[str, dict[str, int]], rows: list[dict]) -> None:
    """Adds sibling counts to each page row for the interface chips."""
    for row in rows:
        row["category_matches"] = counts["category"][_facet_value(row, "category")]
        row["seller_matches"] = counts["seller_id"][_facet_value(row, "seller_id")]
        row["currency_matches"] = counts["currency"][_facet_value(row, "currency")]
        row["band_matches"] = counts["rating_band"][_facet_value(row, "rating_band")]
