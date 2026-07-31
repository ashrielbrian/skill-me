"""Unit tests for the ranking stage."""

from services.catalog import config
from services.catalog.search.rank import rank_page, rerank


def _candidate(product_id, score=1.0, rating=4.0):
    return {"product_id": product_id, "score": score, "rating": rating}


def test_rerank_orders_by_blended_score():
    rows = [_candidate(1, score=0.2), _candidate(2, score=0.9)]
    ranked = rerank(rows, [])
    assert [row["product_id"] for row in ranked] == [2, 1]


def test_promoted_id_gains_boost():
    rows = [_candidate(1, score=0.5), _candidate(2, score=0.6)]
    ranked = rerank(rows, [1])
    assert ranked[0]["product_id"] == 1


def test_non_promoted_order_unchanged():
    rows = [_candidate(3, score=0.8), _candidate(4, score=0.1)]
    ranked = rerank(rows, [99])
    assert [row["product_id"] for row in ranked] == [3, 4]


def test_rating_feeds_into_score():
    rows = [_candidate(1, score=0.5, rating=1.0), _candidate(2, score=0.5, rating=5.0)]
    ranked = rerank(rows, [])
    assert ranked[0]["product_id"] == 2


def test_rank_page_trims_to_page_size():
    rows = [_candidate(i, score=float(i)) for i in range(60)]
    page = rank_page(rows, [])
    assert len(page) == config.SEARCH_PAGE_SIZE
    assert page[0]["product_id"] == 59


def test_rerank_keeps_all_candidates():
    rows = [_candidate(i) for i in range(10)]
    assert len(rerank(rows, [2, 4])) == 10
