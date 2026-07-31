"""Connection settings for the shared Postgres cluster.

Values come from the environment where the deployment sets them
and fall back to the in-datacenter defaults otherwise. Every
service reads the same names, so changing a default here changes
it for the whole fleet.
"""

import os

# Pool sizing shared by every service process.
POOL_MIN = 5
POOL_MAX = 20

# Server-side statement timeout applied to every pooled connection.
STATEMENT_TIMEOUT_MS = 5000

# Seconds allowed for the initial connection handshake.
CONNECT_TIMEOUT_S = 10

# Seconds a caller waits for a free pooled connection.
ACQUIRE_TIMEOUT_S = 30

DSN = os.environ.get(
    "BAZAAR_DATABASE_URL",
    "postgresql://bazaar:bazaar@pg.internal.bazaar:5432/bazaar",
)

# Reported in pg_stat_activity so operators can tell services apart.
APPLICATION_NAME = os.environ.get("BAZAAR_SERVICE_NAME", "bazaar-service")


def as_dict():
    """Return the effective settings for health and startup logs."""
    return {
        "pool_min": POOL_MIN,
        "pool_max": POOL_MAX,
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
        "connect_timeout_s": CONNECT_TIMEOUT_S,
        "application_name": APPLICATION_NAME,
    }
