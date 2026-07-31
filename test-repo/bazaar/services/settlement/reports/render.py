"""Renders the nightly report tables."""

import decimal


def format_money_rows(rows):
    """Returns (label, amount) display pairs for a table."""
    formatted = []
    for row in rows:
        context = decimal.Context(prec=28, rounding=decimal.ROUND_HALF_EVEN)
        cents = context.create_decimal(row["net_cents"])
        amount = cents.scaleb(-2, context=context)
        formatted.append((row["plan"], f"{amount:.2f} USD"))
    return formatted


def table(headers, rows):
    """Returns a fixed-width text table for the report body."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        _row(headers, widths),
        _row(tuple("-" * width for width in widths), widths),
    ]
    for row in rows:
        lines.append(_row(row, widths))
    return "\n".join(lines)


def _row(cells, widths):
    """Returns one padded table line."""
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths))
