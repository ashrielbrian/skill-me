"""Static configuration for the checkout service.

Loaded once at process start; every module imports values from here.
"""

import os

INVENTORY_BASE_URL = "http://inventory.internal:8080"
WEBHOOK_TIMEOUT_S = 5
MAX_LINE_ITEMS = 100

# Connection targets come from the deploy environment, read once at import.
DATABASE_DSN = os.environ.get(
    "CHECKOUT_DATABASE_DSN", "postgresql://checkout@db.internal:5432/bazaar"
)
REDIS_URL = os.environ.get("CHECKOUT_REDIS_URL", "redis://redis.internal:6379/2")

SERVICE_NAME = "checkout"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = int(os.environ.get("CHECKOUT_PORT", "8083"))

ORDER_STATUS_VALUES = ("pending", "reserved", "placed", "settled", "cancelled")
DEFAULT_CURRENCY = "USD"
SUPPORTED_REGIONS = frozenset({"us", "eu", "uk", "ca", "au", "jp"})

MAX_QUANTITY_PER_ITEM = 20
WEBHOOK_MAX_ATTEMPTS = 3
INVENTORY_TIMEOUT_S = 10

LOG_LEVEL = os.environ.get("CHECKOUT_LOG_LEVEL", "INFO")
SHUTDOWN_GRACE_S = 15
