"""Service configuration.

Read once at import; the service reads no environment variables afterward.
"""

import os

# Wire and domain limits.
RESERVE_BATCH_MAX = 100
RESERVATION_TTL_S = 900
GC_BATCH_ROWS = 500

# Seconds between passes of the expired-reservation task in reservations/gc.py.
GC_INTERVAL_S = 60

HTTP_PORT = int(os.environ.get("INVENTORY_PORT", "8083"))

DATABASE_DSN = os.environ.get(
    "INVENTORY_DATABASE_DSN",
    "postgresql://inventory@db.internal:5432/bazaar",
)

POOL_MIN_SIZE = int(os.environ.get("INVENTORY_POOL_MIN", "4"))
POOL_MAX_SIZE = int(os.environ.get("INVENTORY_POOL_MAX", "16"))

LOG_LEVEL = os.environ.get("INVENTORY_LOG_LEVEL", "INFO")

# Request bodies above this size are refused at the framework edge; the
# largest legal batch body sits well under it.
CLIENT_MAX_BODY_BYTES = 64 * 1024

# Seconds aiohttp waits for in-flight requests during shutdown.
SHUTDOWN_GRACE_S = 15
