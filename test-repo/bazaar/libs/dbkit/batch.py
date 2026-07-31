"""Multi-row insert helpers.

``insert_many`` groups rows into flush-sized INSERT statements so
a large load becomes a handful of round trips instead of one per
row. ``build_values_clause`` renders the numbered placeholder
groups for one such statement.
"""

from .executor import execute

# Rows per INSERT; keeps placeholder counts well under the wire limit.
FLUSH_ROWS = 500


def build_values_clause(rows):
    """Return the numbered VALUES placeholders for the given rows."""
    clause = ""
    position = 1
    width = len(rows[0]) if rows else 0
    for row_index in range(len(rows)):
        group = ", ".join("$%d" % (position + offset) for offset in range(width))
        if row_index:
            clause += ", "
        clause += "(" + group + ")"
        position += width
    return clause


def flatten_rows(rows):
    """Return row values in placeholder order for one statement."""
    return [value for row in rows for value in row]


async def insert_many(table, columns, rows):
    """Insert rows into a table, one statement per flush of rows."""
    if not rows:
        return 0
    column_list = ", ".join(columns)
    inserted = 0
    # Table and column names come from code, never from callers' users.
    for start in range(0, len(rows), FLUSH_ROWS):
        chunk = rows[start:start + FLUSH_ROWS]
        clause = build_values_clause(chunk)
        statement = "INSERT INTO %s (%s) VALUES %s" % (table, column_list, clause)
        await execute(statement, flatten_rows(chunk))
        inserted += len(chunk)
    return inserted
