"""Trending search terms for the empty suggest box.

Backed by a Redis sorted set the query-log job maintains; one read
per suggest request that arrives without a prefix.
"""

from services.catalog.cache import client

_TRENDING_KEY = "catalog:trending"

TRENDING_LIMIT = 10


async def trending_terms() -> list[str]:
    """Returns the current leading terms, best first."""
    entries = await client().zrevrange(_TRENDING_KEY, 0, TRENDING_LIMIT - 1)
    return list(entries)


async def record_term(term: str) -> None:
    """Raises one term's weight; the query-log job calls this per
    logged search."""
    cleaned = term.strip().lower()
    if cleaned:
        await client().zincrby(_TRENDING_KEY, 1, cleaned)
