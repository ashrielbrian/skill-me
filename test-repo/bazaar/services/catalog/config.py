"""Static configuration for the catalog service.

Values load once at import time and stay constant for the process
lifetime. Every module in the service reads from this module.
"""

import os

SERVICE_NAME = "catalog"

# Search paging and ranking widths.
SEARCH_PAGE_SIZE = 25
RANK_CANDIDATES = 500
SEMANTIC_RESCORE = False
PROMOTED_REFRESH_MINUTES = 60
DETAIL_CACHE_TTL_S = 60

# Secondary endpoint limits.
SUGGEST_LIMIT = 10
FEED_BATCH_SIZE = 200
MAX_QUERY_LENGTH = 128
MAX_TERMS = 12

# Connection targets; the environment overrides these in every deploy.
DATABASE_DSN = os.environ.get(
    "CATALOG_DATABASE_DSN", "postgresql://catalog@db.internal:5432/bazaar"
)
REDIS_URL = os.environ.get("CATALOG_REDIS_URL", "redis://redis.internal:6379/2")
PRICING_BASE_URL = os.environ.get(
    "CATALOG_PRICING_BASE_URL", "http://pricing.internal:8080"
)

HTTP_PORT = int(os.environ.get("CATALOG_HTTP_PORT", "8081"))
REQUEST_TIMEOUT_S = 5.0
