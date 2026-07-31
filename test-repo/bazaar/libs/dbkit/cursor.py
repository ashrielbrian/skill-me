"""Keyset pagination for reading large tables in pages.

Callers fetch one page at a time and feed the returned key back
in, so each page is a single indexed round trip and no server
cursor stays open between requests. The base query must name its
table and columns but carry no WHERE clause of its own; filters
belong in a view or a wrapped subquery.
"""

import re

from .executor import fetch_all

DEFAULT_PAGE_ROWS = 500

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def check_key_column(name):
    """Key columns are code-supplied identifiers; reject anything else."""
    if not _IDENTIFIER.match(name or ""):
        raise ValueError("not a plain column identifier: %r" % name)
    return name


async def fetch_page(base_sql, key_column="id", after=None, limit=DEFAULT_PAGE_ROWS):
    """Return ``(rows, next_key)`` for one page ordered by the key.

    ``next_key`` is None when the page came back short, meaning the
    scan reached the end of the table. Otherwise pass it back as
    ``after`` to continue where this page stopped.
    """
    check_key_column(key_column)
    if after is None:
        statement = "%s ORDER BY %s LIMIT %d" % (base_sql, key_column, limit)
        rows = await fetch_all(statement)
    else:
        statement = "%s WHERE %s > $1 ORDER BY %s LIMIT %d" % (
            base_sql,
            key_column,
            key_column,
            limit,
        )
        rows = await fetch_all(statement, (after,))
    if len(rows) < limit:
        return rows, None
    return rows, rows[-1][key_column]


def page_count_estimate(total_rows, limit=DEFAULT_PAGE_ROWS):
    """How many pages a scan of ``total_rows`` will take."""
    if total_rows <= 0:
        return 0
    return (total_rows + limit - 1) // limit
