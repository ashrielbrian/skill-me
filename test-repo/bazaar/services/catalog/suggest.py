"""Autocomplete suggestions for the search box.

Each keystroke past two characters issues one request; the database
applies the prefix filter, ordering, and limit.
"""

from libs.dbkit.executor import execute, fetch_all
from services.catalog import config

MIN_PREFIX = 2

_SUGGEST_SQL = (
    "SELECT term FROM suggest_terms "
    "WHERE term LIKE $1 || '%' "
    "ORDER BY weight DESC LIMIT $2"
)


async def suggestions(prefix: str) -> list[str]:
    """Returns ranked completions for one prefix.

    Runs once per suggest request.
    """
    cleaned = prefix.strip().lower()
    if len(cleaned) < MIN_PREFIX:
        return []
    rows = await fetch_all(_SUGGEST_SQL, (cleaned, config.SUGGEST_LIMIT))
    return [row["term"] for row in rows]


async def record_selection(term: str) -> None:
    """Raises a term's weight after a shopper picks it.

    Runs on a small fraction of suggest traffic.
    """
    cleaned = term.strip().lower()
    if not cleaned:
        return
    await execute(
        "UPDATE suggest_terms SET weight = weight + 1 WHERE term = $1",
        (cleaned,),
    )
