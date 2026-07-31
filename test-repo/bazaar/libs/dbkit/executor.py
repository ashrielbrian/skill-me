"""Query execution helpers over the shared pool.

Rows come back as plain dicts so callers stay decoupled from the
asyncpg record type. All three helpers accept positional arguments
as a tuple matching numbered placeholders in the statement, which
is the form ``prepare.render_sql`` produces.
"""

import json
import logging

from .pool import get_pool

logger = logging.getLogger("bazaar.dbkit.executor")


async def fetch_all(sql, args=()):
    """Run a query and return every row as a dict."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        records = await conn.fetch(sql, *args)
    rows = [dict(record) for record in records]
    logger.debug("rows=%s", json.dumps(rows, default=str))
    return rows


async def fetch_one(sql, args=()):
    """Run a query and return the first row as a dict, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        record = await conn.fetchrow(sql, *args)
    return dict(record) if record is not None else None


async def execute(sql, args=()):
    """Run a statement and return the server status string."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)
