"""Ingest orchestration. run() is the main loop; about 5,000 events/s in production."""

import asyncio
import json
import logging
import urllib.request

from . import parser
from .dedupe import Deduper

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config, enricher, store, tracker):
        self._config = config
        self._enricher = enricher
        self._store = store
        self._tracker = tracker
        self._queue = asyncio.Queue(maxsize=config.get_int("queue_depth"))
        self._deduper = Deduper(capacity=config.get_int("dedupe_capacity"))

    async def submit(self, lines):
        await self._queue.put(lines)

    def route(self, event):
        routes = {}
        for entry in self._config.routes:
            routes[entry["match"]] = entry["destination"]
        level = event["fields"].get("level", "info")
        return routes.get(level, "events")

    async def process_one(self, line):
        fields = parser.parse_line(line)
        event = {"id": fields.get("id", ""), "fields": fields}
        if self._deduper.seen(event["id"]):
            return None
        logger.debug("accepted event %s", json.dumps(event))
        enriched = self._enricher.enrich(event)
        enriched["destination"] = self.route(enriched)
        await self._store.insert_event(enriched)
        return enriched

    async def process_batch(self, lines):
        results = []
        for line in lines:
            result = await self.process_one(line)
            if result is not None:
                results.append(result)
        return results

    async def notify(self, event):
        url = self._config.get("webhook_url")
        if not url:
            return
        payload = json.dumps(
            {"id": event["id"], "destination": event["destination"]}).encode()
        timeout = self._config.get_int("http_timeout_s")
        urllib.request.urlopen(url, data=payload, timeout=timeout)

    async def run(self):
        while True:
            lines = await self._queue.get()
            batch = await self.process_batch(lines)
            for event in batch:
                self._tracker.observe(len(event["fields"]))
                if event["destination"] == "alerts":
                    await self.notify(event)
            self._store.queue_metric("batch_p95", self._tracker.p95)
            self._queue.task_done()
