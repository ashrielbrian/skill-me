"""Static configuration for the pricing worker.

All values are read once at import time; nothing here changes while a
worker process is running.
"""

import os

STREAM_NAME = os.environ.get("PRICING_STREAM", "events:pricing")
GROUP_NAME = os.environ.get("PRICING_GROUP", "pricing-workers")
CONSUMER_NAME = os.environ.get("PRICING_CONSUMER", "worker-1")

REDIS_HOST = os.environ.get("REDIS_HOST", "redis.internal")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# Number of stream entries requested per read.
STREAM_READ_COUNT = 100

# Redis key holding the serialized promo ruleset.
PROMO_SNAPSHOT_KEY = "promo:rules"

# Number of latency samples kept for the p99 estimate.
STATS_WINDOW = 2048

# Recently seen event ids kept for duplicate suppression.
DEDUPE_CAPACITY = 8192

IDLE_SLEEP_SECONDS = 0.05

# All stored prices are normalized into this currency.
SETTLEMENT_CURRENCY = "USD"

REGION_CURRENCIES = {
    "us": "USD",
    "eu": "EUR",
    "uk": "GBP",
    "jp": "JPY",
    "au": "AUD",
}

CATALOG_BASE_URL = os.environ.get("CATALOG_URL", "http://catalog.internal:8002")

# Rows returned by the price-history lookup in the admin CLI.
HISTORY_RECENT_LIMIT = 100
