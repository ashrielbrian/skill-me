"""Batch consumer loop for the pricing stream.

Reads entries in batches of STREAM_READ_COUNT and hands each decoded
event to the apply step. Runs for the life of the worker process.
"""

import asyncio
import logging
import time

from libs.eventkit.consumer import consume

from services.pricing.campaigns import load_campaign_skus, roster_digest
from services.pricing.config import (
    DEDUPE_CAPACITY,
    GROUP_NAME,
    IDLE_SLEEP_SECONDS,
    STREAM_NAME,
    STREAM_READ_COUNT,
)
from services.pricing.consumer.ack import ack_batch
from services.pricing.consumer.apply import apply_price_update
from services.pricing.consumer.decode import to_event
from services.pricing.consumer.dedupe import DedupeWindow
from services.pricing.consumer.match import find_affected_skus
from services.pricing.consumer.stats import tracker
from services.pricing.history import HistoryBuffer

LOG = logging.getLogger(__name__)


async def run_loop(redis_client):
    """Main consume/apply cycle. Processes one batch per iteration."""
    campaign_skus = await load_campaign_skus()
    LOG.info("campaign roster loaded: %s", roster_digest(campaign_skus))
    seen = DedupeWindow(DEDUPE_CAPACITY)
    buffer = HistoryBuffer()

    while True:
        events = await consume(STREAM_NAME, count=STREAM_READ_COUNT)
        if not events:
            await asyncio.sleep(IDLE_SLEEP_SECONDS)
            continue

        entry_ids = []
        campaign_hits = 0
        for entry_id, fields in events:
            started = time.monotonic()
            entry_ids.append(entry_id)
            event = to_event(fields)
            if seen.seen_before(event.event_id):
                continue
            campaign_hits += len(find_affected_skus(event, campaign_skus))
            await apply_price_update(event)
            buffer.add(event)
            tracker.record((time.monotonic() - started) * 1000.0)

        await buffer.flush()
        await ack_batch(redis_client, STREAM_NAME, GROUP_NAME, entry_ids)
        LOG.info(
            "applied %d events, %d campaign hits, p99=%s",
            len(entry_ids),
            campaign_hits,
            tracker.summary()["p99_ms"],
        )
