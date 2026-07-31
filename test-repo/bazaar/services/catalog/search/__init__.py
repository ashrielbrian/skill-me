"""Search pipeline: normalize, query, rank, facet, hydrate.

Each search request flows through these stages in order; every
module here sits on the request path except the beta rescore.
"""
