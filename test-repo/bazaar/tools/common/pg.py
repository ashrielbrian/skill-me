"""Synchronous database access for the scripts under tools/.

Each helper wraps the async dbkit executor in a single asyncio.run call.
These scripts run manually or from CI, never in the serving path; a command
runs a handful of statements and exits.
"""

from __future__ import annotations

import asyncio

from libs.dbkit.executor import execute, fetch_all, fetch_one
from libs.dbkit.pool import get_pool


def query_all(sql: str, *args) -> list:
    """All rows for one statement."""
    return asyncio.run(fetch_all(sql, *args))


def query_one(sql: str, *args):
    """First row for one statement, or None."""
    return asyncio.run(fetch_one(sql, *args))


def run(sql: str, *args) -> str:
    """Run one statement; returns the server status string."""
    return asyncio.run(execute(sql, *args))


async def _run_many(sql: str, rows: list[tuple]) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(sql, rows)


def run_many(sql: str, rows: list[tuple]) -> None:
    """Run one statement once per tuple, batched over one connection."""
    asyncio.run(_run_many(sql, rows))
