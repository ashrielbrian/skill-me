"""Process wiring. main() runs once at startup."""

import asyncio
import sqlite3

from . import config as config_mod
from .ingest.enrich import Enricher
from .ingest.pipeline import Pipeline
from .metrics import LatencyTracker
from .query.search import Searcher
from .store.db import EventStore


def read_region_csv(cfg):
    try:
        with open(cfg.get("region_csv")) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def build_query_index(conn):
    index = {}
    cur = conn.execute("SELECT id, tags FROM events")
    for event_id, tags in cur.fetchall():
        for tag in (tags or "").split(","):
            index.setdefault(tag, []).append(event_id)
    return index


def main(mode="all", config_path="perfburn.ini"):
    cfg = config_mod.load(config_path)
    conn = sqlite3.connect(cfg.get("db_path"))
    index = build_query_index(conn)

    store = None
    pipeline = None
    searcher = None

    if mode in ("all", "ingest"):
        store = EventStore(
            cfg.get("db_path"),
            metric_flush_rows=cfg.get_int("metric_flush_rows"))
        enricher = Enricher(store.connection, read_region_csv(cfg))
        pipeline = Pipeline(cfg, enricher, store, LatencyTracker(
            window=cfg.get_int("sample_window")))

    if mode in ("all", "query"):
        searcher = Searcher(conn)

    return {"config": cfg, "pipeline": pipeline, "store": store,
            "searcher": searcher, "index": index}


def run_ingest(wiring, lines_source):
    async def _run():
        task = asyncio.ensure_future(wiring["pipeline"].run())
        async for lines in lines_source:
            await wiring["pipeline"].submit(lines)
        task.cancel()

    asyncio.run(_run())


def run_report(day, config_path="perfburn.ini"):
    from .query import reportgen

    cfg = config_mod.load(config_path)
    conn = sqlite3.connect(cfg.get("db_path"))
    print(reportgen.render(conn, day))
    conn.close()
