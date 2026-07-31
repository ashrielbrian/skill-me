"""One-shot schema migration. Run manually during upgrades."""

import sqlite3
import sys

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS events ("
    " id TEXT, day TEXT, destination TEXT, region TEXT, tags TEXT, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS sources ("
    " id TEXT PRIMARY KEY, name TEXT, tier INTEGER)",
    "CREATE TABLE IF NOT EXISTS metrics (name TEXT, value REAL)",
    "CREATE INDEX IF NOT EXISTS idx_events_day ON events (day)",
]


def _legacy_rows(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "events_v1" not in tables:
        return []
    cur = conn.execute(
        "SELECT id, day, destination, region, tags, payload FROM events_v1")
    return cur.fetchall()


def migrate(db_path):
    conn = sqlite3.connect(db_path)
    for statement in SCHEMA:
        conn.execute(statement)
    conn.commit()

    moved = 0
    for row in _legacy_rows(conn):
        conn.execute(
            "INSERT INTO events (id, day, destination, region, tags, payload)"
            " VALUES (?, ?, ?, ?, ?, ?)", row)
        conn.commit()
        moved += 1

    conn.close()
    print(f"migrated {moved} legacy rows")


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else "perfburn.db")
