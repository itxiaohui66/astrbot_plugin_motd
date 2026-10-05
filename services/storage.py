"""SQLite persistence outside the plugin checkout; every connection stays in a worker thread."""

import asyncio
import sqlite3
import time
from pathlib import Path


class MotdStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _run(self, operation):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=15) as db:
            db.row_factory = sqlite3.Row
            return operation(db)

    async def initialize(self):
        def create(db):
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bindings (
                    scope TEXT PRIMARY KEY, address TEXT NOT NULL,
                    updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tracked (
                    address TEXT PRIMARY KEY, touched_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    address TEXT NOT NULL, timestamp INTEGER NOT NULL,
                    online INTEGER, players INTEGER, source TEXT NOT NULL,
                    PRIMARY KEY (address, timestamp)
                );
            """)

        await asyncio.to_thread(self._run, create)

    async def get_binding(self, scope: str) -> str | None:
        def query(db):
            row = db.execute("SELECT address FROM bindings WHERE scope=?", (scope,)).fetchone()
            return row[0] if row else None

        return await asyncio.to_thread(self._run, query)

    async def set_binding(self, scope: str, address: str, user: str):
        await asyncio.to_thread(
            self._run,
            lambda db: (
                db.execute(
                    "INSERT OR REPLACE INTO bindings VALUES (?, ?, ?, ?)",
                    (scope, address, user, int(time.time())),
                ).rowcount
            ),
        )

    async def clear_binding(self, scope: str):
        await asyncio.to_thread(
            self._run,
            lambda db: (
                db.execute(
                    "DELETE FROM bindings WHERE scope=?",
                    (scope,),
                ).rowcount
            ),
        )

    async def track(self, address: str, limit: int = 128):
        def insert(db):
            db.execute("INSERT OR REPLACE INTO tracked VALUES (?, ?)", (address, int(time.time())))
            db.execute(
                """DELETE FROM tracked WHERE address IN (
                SELECT address FROM tracked ORDER BY touched_at DESC, address LIMIT -1 OFFSET ?
            )""",
                (limit,),
            )

        await asyncio.to_thread(self._run, insert)

    async def monitored_addresses(self, now: int | None = None) -> list[str]:
        now = int(time.time()) if now is None else now
        return await asyncio.to_thread(
            self._run,
            lambda db: [
                r[0]
                for r in db.execute(
                    "SELECT address FROM bindings UNION SELECT address FROM tracked WHERE touched_at>=?",
                    (now - 86400,),
                )
            ],
        )

    async def record(self, address: str, data: dict | None, timestamp: int | None = None):
        timestamp = int(time.time()) if timestamp is None else timestamp
        online = int(bool(data["online"])) if data is not None else None
        players = int(data.get("players_online", 0)) if online else None
        source = data.get("source", "api") if data else "error"
        await asyncio.to_thread(
            self._run,
            lambda db: (
                db.execute(
                    "INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?)",
                    (address, timestamp, online, players, source),
                ).rowcount
            ),
        )

    async def history(self, address: str, now: int | None = None) -> list[dict]:
        now = int(time.time()) if now is None else now
        return await asyncio.to_thread(
            self._run,
            lambda db: [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM samples WHERE address=? AND timestamp BETWEEN ? AND ? ORDER BY timestamp",
                    (address, now - 86400, now),
                )
            ],
        )

    async def prune(self, retention_hours: int = 48):
        now = int(time.time())

        def clean(db):
            db.execute("DELETE FROM samples WHERE timestamp<?", (now - retention_hours * 3600,))
            db.execute("DELETE FROM tracked WHERE touched_at<?", (now - 86400,))

        await asyncio.to_thread(self._run, clean)
