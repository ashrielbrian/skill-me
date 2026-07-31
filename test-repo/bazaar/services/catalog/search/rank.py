"""Ordering of search candidates.

Blends the text score, rating, and promoted placements, then trims
to one page. Runs once per search request over up to RANK_CANDIDATES
rows.
"""

from services.catalog import config
from services.catalog.search.semantic import embeddings_for, semantic_rescore

# Weights settled during the 2025 relevance review.
_RATING_WEIGHT = 0.15
_PROMOTED_BOOST = 2.5


def rerank(candidates: list[dict], promoted_ids: list[int]) -> list[dict]:
    """Scores and orders one candidate set.

    Runs once per search request; promoted placements gain a fixed
    addition on top of the text score.
    """
    scored = []
    for candidate in candidates:
        score = float(candidate.get("score", 0.0))
        score += _RATING_WEIGHT * float(candidate.get("rating", 0.0))
        if candidate["product_id"] in promoted_ids:
            score += _PROMOTED_BOOST
        candidate["score"] = score
        scored.append(candidate)
    scored.sort(key=_sort_key, reverse=True)
    return scored


def _sort_key(candidate: dict):
    """Orders by blended score with the id as a stable tie break."""
    return (candidate["score"], candidate["product_id"])


def head(rows: list[dict], limit: int) -> list[dict]:
    """Returns the leading rows up to a limit without copying dicts."""
    return rows[:limit]


def rank_page(
    candidates: list[dict], promoted_ids: list[int], tenant: str = ""
) -> list[dict]:
    """Produces the final ordered page for one search response."""
    ranked = rerank(candidates, promoted_ids)
    if config.SEMANTIC_RESCORE:
        ranked = semantic_rescore(ranked, embeddings_for(tenant))
    return head(ranked, config.SEARCH_PAGE_SIZE)
