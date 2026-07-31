"""Category tree access.

The tree holds a few hundred nodes and changes rarely; it reloads
from Postgres at most once per hour and serves every categories
request in between from memory.
"""

import time

from libs.dbkit.executor import fetch_all

_REFRESH_SECONDS = 3600.0

_tree: list[dict] = []
_loaded_at = 0.0


def _build_tree(rows) -> list[dict]:
    """Shapes flat parent/child rows into a nested list."""
    nodes = {
        row["id"]: {"id": row["id"], "name": row["name"], "children": []}
        for row in rows
    }
    roots = []
    for row in rows:
        node = nodes[row["id"]]
        parent = nodes.get(row["parent_id"])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


async def category_tree() -> list[dict]:
    """Returns the nested category list.

    Runs once per categories request; the reload query runs at most
    once per hour.
    """
    global _tree, _loaded_at
    now = time.monotonic()
    if not _tree or now - _loaded_at >= _REFRESH_SECONDS:
        rows = await fetch_all(
            "SELECT id, name, parent_id FROM categories ORDER BY sort_order"
        )
        _tree = _build_tree(rows)
        _loaded_at = now
    return _tree


def flatten(tree: list[dict]) -> list[str]:
    """Depth-first list of category names; the feed builder uses it."""
    names = []
    stack = list(tree)
    while stack:
        node = stack.pop()
        names.append(node["name"])
        stack.extend(node["children"])
    return names
