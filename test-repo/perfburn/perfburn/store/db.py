"""SQLite event storage. insert_event() runs once per event."""

import asyncio
import sqlite3

from ..ingest import parser


class EventStore:
    def __init__(self, path, metric_flush_rows=200):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._pending_metrics = []
        self._metric_flush_rows = metric_flush_rows

    @property
    def connection(self):
        return self._conn

    def _insert_sync(self, event):
        self._conn.execute(
            "INSERT INTO events (id, day, destination, region, tags, payload)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (event["id"], event["day"], event["destination"], event["region"],
             event["fields"].get("tags", ""), parser.normalize(event["fields"])))
        self._conn.commit()

    async def insert_event(self, event):
        await asyncio.to_thread(self._insert_sync, event)

    def queue_metric(self, name, value):
        self._pending_metrics.append((name, value))
        if len(self._pending_metrics) >= self._metric_flush_rows:
            self.flush_metrics()

    def flush_metrics(self):
        if not self._pending_metrics:
            return
        self._conn.executemany(
            "INSERT INTO metrics (name, value) VALUES (?, ?)",
            self._pending_metrics)
        self._conn.commit()
        self._pending_metrics.clear()

    def close(self):
        self.flush_metrics()
        self._conn.close()
