"""Configuration loading. load() runs once at process start."""

import os
import re

DEFAULTS = {
    "db_path": "perfburn.db",
    "batch_size": "500",
    "flush_interval_ms": "200",
    "webhook_url": "",
    "region_csv": "regions.csv",
    "queue_depth": "10000",
    "log_level": "INFO",
    "archive_dir": "archive",
    "retention_days": "30",
    "sample_window": "1024",
    "dedupe_capacity": "50000",
    "metric_flush_rows": "200",
    "search_page_size": "100",
    "report_sections": "4",
    "http_timeout_s": "5",
    "ingest_workers": "1",
    "day_format": "%Y-%m-%d",
    "source_cache_rows": "0",
    "alert_level": "error",
    "listen_port": "8125",
}

DEFAULT_ROUTES = [
    {"match": "error", "destination": "alerts"},
    {"match": "warn", "destination": "alerts"},
    {"match": "info", "destination": "events"},
    {"match": "debug", "destination": "events"},
]


class Config:
    def __init__(self, values, routes):
        self._values = values
        self.routes = routes

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_int(self, key):
        return int(self._values[key])


def load(path="perfburn.ini"):
    text = ""
    if os.path.exists(path):
        with open(path) as f:
            text = f.read()
    values = {}
    for key, default in DEFAULTS.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=\s*(.+)$", re.MULTILINE)
        match = pattern.search(text)
        values[key] = match.group(1).strip() if match else default
    return Config(values, list(DEFAULT_ROUTES))
