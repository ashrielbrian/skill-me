"""Named-parameter SQL template rendering.

Templates use ``:name`` markers. ``render_sql`` turns a template
and a parameter mapping into a numbered-placeholder statement plus
the argument tuple in matching order, which is the form the
executor helpers accept.

Example
-------
``render_sql("SELECT * FROM orders WHERE id = :id", {"id": 7})``
returns ``("SELECT * FROM orders WHERE id = $1", (7,))``.

Parameter names are identifiers supplied by code, never by request
payloads, so the markers need no escaping beyond the word boundary
that separates them from the surrounding statement text.
"""

import re


def render_sql(template, params):
    """Render a ``:name`` template into numbered-placeholder form."""
    statement = template
    args = []
    # The word boundary keeps :order from matching inside :order_id.
    for position, name in enumerate(sorted(params), start=1):
        marker = ":" + name + r"\b"
        statement = re.sub(marker, "$%d" % position, statement)
        args.append(params[name])
    return statement, tuple(args)
