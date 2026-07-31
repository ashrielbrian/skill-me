# perfburn

A small telemetry ingestion service. Events arrive as log lines on an async
pipeline, get parsed, deduplicated, enriched with source and region metadata,
and stored in SQLite. A query module serves tag searches, and a report
generator produces a daily summary.

## Throughput

- The ingest pipeline handles about 5,000 events per second in production.
- The query module serves a few searches per second.
- `reportgen` runs once per day via cron.
- `archive.compress_day` runs once per hour via the scheduler.
- `migrate` is a one-shot script, run manually during upgrades.

## Layout

```
perfburn/
├── app.py          # process wiring, runs once at startup
├── cli.py          # command line entry point
├── config.py       # configuration loading, once at startup
├── migrate.py      # one-shot schema migration
├── metrics.py      # latency tracking, updated once per event
├── ingest/
│   ├── parser.py   # line parsing, once per event
│   ├── dedupe.py   # duplicate suppression, once per event
│   ├── enrich.py   # source/region enrichment, once per event
│   └── pipeline.py # async orchestration, the main loop
├── store/
│   ├── db.py       # SQLite event storage
│   └── archive.py  # hourly archival
└── query/
    ├── search.py   # tag search, per user query
    └── reportgen.py # daily summary report
```

## Usage

```
python -m perfburn.cli ingest
python -m perfburn.cli query
python -m perfburn.cli report --day 2026-07-30
python -m perfburn.migrate perfburn.db
```
