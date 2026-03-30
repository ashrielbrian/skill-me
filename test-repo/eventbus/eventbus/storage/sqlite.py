"""SQLite-backed event storage."""
from __future__ import annotations
import json
import aiosqlite
from typing import Optional
from ..event import Event


class SQLiteStorage:
    """Persistent event storage using SQLite."""

    def __init__(self, db_path: str = "events.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Create the database and table if needed."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp REAL NOT NULL,
                source TEXT DEFAULT ''
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_topic ON events(topic)"
        )
        await self._db.commit()

    async def store(self, event: Event) -> None:
        """Store an event."""
        if self._db is None:
            raise RuntimeError("Storage not initialized. Call initialize() first.")
        # BUG [CORRECTNESS]: json.dumps(event.payload) can fail if payload contains
        # non-serializable types (datetime, bytes, custom objects). No error handling.
        await self._db.execute(
            "INSERT OR REPLACE INTO events (event_id, topic, payload, timestamp, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (event.event_id, event.topic, json.dumps(event.payload),
             event.timestamp, event.source)
        )
        await self._db.commit()  # BUG [PERFORMANCE]: Commits after every single insert

    async def get_by_topic(self, topic: str, limit: int = 100) -> list[Event]:
        """Get recent events for a topic."""
        if self._db is None:
            raise RuntimeError("Storage not initialized.")
        # BUG [SECURITY]: SQL injection via topic parameter if this were string-formatted.
        # Actually, this uses parameterized queries correctly — NOT a bug. This is a
        # false positive trap for the auditor.
        cursor = await self._db.execute(
            "SELECT event_id, topic, payload, timestamp, source "
            "FROM events WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
            (topic, limit)
        )
        rows = await cursor.fetchall()
        return [Event(
            event_id=r[0], topic=r[1], payload=json.loads(r[2]),
            timestamp=r[3], source=r[4]
        ) for r in rows]

    async def count(self) -> int:
        """Get total stored events."""
        if self._db is None:
            raise RuntimeError("Storage not initialized.")
        cursor = await self._db.execute("SELECT COUNT(*) FROM events")
        row = await cursor.fetchone()
        return row[0]

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # BUG [CORRECTNESS]: No __aenter__/__aexit__ — can't use as async context manager
    # despite common pattern of using storage with `async with`. Also no finalizer,
    # so if close() is never called, the connection leaks.
