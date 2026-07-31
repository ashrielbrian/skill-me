"""Rendering for command-line tools: aligned tables and JSON."""

from __future__ import annotations

import json
import sys


def render_table(headers: tuple, rows: list) -> str:
    """Text table with columns sized to the widest cell."""
    cells = [[str(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    header_line = "  ".join(header.ljust(width) for header, width in zip(headers, widths))
    rule = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(value.ljust(width) for value, width in zip(row, widths))
        for row in cells
    ]
    return "\n".join([header_line, rule, *body])


def print_table(headers: tuple, rows: list) -> None:
    print(render_table(headers, rows))


def print_json(payload) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
