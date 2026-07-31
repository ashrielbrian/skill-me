"""Static configuration for the gateway edge.

Values load once at import time; the process restarts to pick up changes.
"""
import os

SERVICE_NAME = "gateway"

LISTEN_HOST = os.environ.get("GATEWAY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("GATEWAY_LISTEN_PORT", "8080"))

# Internal service addresses; all traffic stays inside one datacenter.
AUTH_BASE_URL = "http://auth.internal:8443"
CATALOG_BASE_URL = "http://catalog.internal:8081"
CHECKOUT_BASE_URL = "http://checkout.internal:8082"

RATE_RULES_PATH = "config/rate_rules.json"
DEFAULT_RATE_LIMIT_PER_MIN = 600

SESSION_CACHE_TTL_S = 300
SESSION_CACHE_MAX = 10000

REQUEST_TIMEOUT_S = 10

# Paging caps applied before a search reaches catalog.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

ALLOWED_ORIGINS = frozenset({
    "https://bazaar.example",
    "https://admin.bazaar.example",
    "https://m.bazaar.example",
})

ACCESS_LOG_NAME = "gateway.access"

# Header the load balancer uses to hand us the original client address.
CLIENT_ADDR_HEADER = "x-forwarded-for"
