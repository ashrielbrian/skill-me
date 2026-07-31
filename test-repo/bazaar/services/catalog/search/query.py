"""SQL construction and execution for the search path.

The database applies the text filter, category filter, ordering, and
the row limit; only the listing columns cross the wire.
"""

from libs.dbkit.executor import fetch_all
from services.catalog import config

# The eight listing columns the response layer reads.
SEARCH_COLUMNS = (
    "id AS product_id, name, price_cents, currency, "
    "seller_id, category, thumbnail_url, rating"
)

_PAGE_SQL = (
    "SELECT " + SEARCH_COLUMNS + " FROM products "
    "WHERE status = 'active' "
    "AND search_text @@ plainto_tsquery('simple', $1) "
    "ORDER BY rating DESC, id LIMIT $2"
)

_PAGE_SQL_WITH_CATEGORY = (
    "SELECT " + SEARCH_COLUMNS + " FROM products "
    "WHERE status = 'active' AND category = $2 "
    "AND search_text @@ plainto_tsquery('simple', $1) "
    "ORDER BY rating DESC, id LIMIT $3"
)

_CANDIDATE_SQL = (
    "SELECT " + SEARCH_COLUMNS + ", "
    "ts_rank(search_text, plainto_tsquery('simple', $1)) AS score "
    "FROM products WHERE status = 'active' "
    "AND search_text @@ plainto_tsquery('simple', $1) "
    "ORDER BY score DESC LIMIT $2"
)

_CANDIDATE_SQL_WITH_CATEGORY = (
    "SELECT " + SEARCH_COLUMNS + ", "
    "ts_rank(search_text, plainto_tsquery('simple', $1)) AS score "
    "FROM products WHERE status = 'active' AND category = $3 "
    "AND search_text @@ plainto_tsquery('simple', $1) "
    "ORDER BY score DESC LIMIT $2"
)


async def search_page(terms: list[str], category: str | None = None):
    """Runs the main search query for the unranked path.

    Runs once per plain search request; the page limit binds to
    SEARCH_PAGE_SIZE.
    """
    text = " ".join(terms)
    if category is None:
        return await fetch_all(_PAGE_SQL, (text, config.SEARCH_PAGE_SIZE))
    return await fetch_all(
        _PAGE_SQL_WITH_CATEGORY, (text, category, config.SEARCH_PAGE_SIZE)
    )


async def candidate_rows(terms: list[str], category: str | None = None):
    """Fetches the candidate set for the ranked path.

    Runs once per ranked search request; the limit binds to
    RANK_CANDIDATES.
    """
    text = " ".join(terms)
    if category is None:
        rows = await fetch_all(_CANDIDATE_SQL, (text, config.RANK_CANDIDATES))
    else:
        rows = await fetch_all(
            _CANDIDATE_SQL_WITH_CATEGORY, (text, config.RANK_CANDIDATES, category)
        )
    return [dict(row) for row in rows]
