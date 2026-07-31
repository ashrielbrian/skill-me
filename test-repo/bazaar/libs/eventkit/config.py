"""Settings for the Redis stream transport.

As with dbkit, values come from the environment with datacenter
defaults, and every service reads the same names.
"""

import os

# Events buffered per producer before one pipelined delivery.
PUBLISH_FLUSH_COUNT = 200

# How long one consumer read waits for new entries, in milliseconds.
CONSUMER_BLOCK_MS = 1000

REDIS_URL = os.environ.get(
    "BAZAAR_REDIS_URL",
    "redis://redis.internal.bazaar:6379/0",
)

# Streams are trimmed to roughly this many entries on write.
STREAM_MAXLEN = 1_000_000

# Every service's workers join the same group per stream.
CONSUMER_GROUP = os.environ.get("BAZAAR_CONSUMER_GROUP", "bazaar-workers")

STREAM_PREFIX = "bazaar"


def as_dict():
    """Return the effective settings for health and startup logs."""
    return {
        "publish_flush_count": PUBLISH_FLUSH_COUNT,
        "consumer_block_ms": CONSUMER_BLOCK_MS,
        "stream_maxlen": STREAM_MAXLEN,
        "consumer_group": CONSUMER_GROUP,
        "stream_prefix": STREAM_PREFIX,
    }
