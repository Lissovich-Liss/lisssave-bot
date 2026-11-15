"""Asynchronous SQLite database layer using aiosqlite."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from loguru import logger

from bot.config import settings


@dataclass(slots=True)
class BotStatistics:
    """Aggregated statistics for the admin dashboard."""

    total_users: int
    active_users: int
    new_users_24h: int
    new_users_7d: int
    total_downloads: int
    downloads_24h: int
    total_bytes: int
    top_platforms: list[tuple[str, int]]


class Database:
    """Async SQLite manager for users, downloads, and temporary request state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Initialize database schema and enable WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    language_code TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    url TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    quality TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_requests (
                    req_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    duration INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_created_at ON downloads (created_at);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_platform ON downloads (platform);"
            )
            # Prune stale media_requests older than 48 hours
            await conn.execute(
                "DELETE FROM media_requests WHERE created_at < datetime('now', '-48 hours');"
            )
            await conn.commit()
        logger.info("SQLite database initialized at {}", self.db_path)

    async def upsert_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        language_code: str | None = None,
    ) -> bool:
        """Insert a new user or update profile fields if already registered.

        Returns True if the user was newly created.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?;",
                (user_id,),
            )
            exists = await cursor.fetchone() is not None

            if not exists:
                await conn.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, language_code, is_active)
                    VALUES (?, ?, ?, ?, 1);
                    """,
                    (user_id, username, first_name, language_code),
                )
            else:
                await conn.execute(
                    """
                    UPDATE users
                    SET username = ?,
                        first_name = ?,
                        language_code = COALESCE(?, language_code),
                        is_active = 1,
                        last_active_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?;
                    """,
                    (username, first_name, language_code, user_id),
                )
            await conn.commit()
            return not exists

    async def set_user_active(self, user_id: int, is_active: bool) -> None:
        """Update user active status (e.g., when bot is blocked or unblocked)."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE users SET is_active = ? WHERE user_id = ?;",
                (1 if is_active else 0, user_id),
            )
            await conn.commit()

    async def get_active_user_ids(self) -> list[int]:
        """Return list of all active user IDs for broadcast messaging."""
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT user_id FROM users WHERE is_active = 1 ORDER BY created_at ASC;"
            )
            rows = await cursor.fetchall()
            return [int(row[0]) for row in rows]

    async def log_download(
        self,
        user_id: int,
        platform: str,
        url: str,
        media_type: str,
        quality: str,
        file_size: int = 0,
    ) -> None:
        """Record a completed download in the database."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT INTO downloads (user_id, platform, url, media_type, quality, file_size)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (user_id, platform, url, media_type, quality, file_size),
            )
            await conn.execute(
                "UPDATE users SET last_active_at = CURRENT_TIMESTAMP WHERE user_id = ?;",
                (user_id,),
            )
            await conn.commit()

    async def save_media_request(
        self,
        req_id: str,
        user_id: int,
        url: str,
        platform: str,
        title: str,
        duration: int,
        metadata: dict[str, Any],
    ) -> None:
        """Persist extracted URL metadata mapped to a short request ID."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO media_requests
                    (req_id, user_id, url, platform, title, duration, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    req_id,
                    user_id,
                    url,
                    platform,
                    title,
                    duration,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            await conn.commit()

    async def get_media_request(self, req_id: str) -> dict[str, Any] | None:
        """Retrieve a previously stored media request by its short ID."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM media_requests WHERE req_id = ?;",
                (req_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            data = dict(row)
            try:
                data["metadata"] = json.loads(data.pop("metadata_json", "{}"))
            except json.JSONDecodeError:
                data["metadata"] = {}
            return data

    async def get_statistics(self) -> BotStatistics:
        """Compute user and download statistics for the /admin dashboard."""
        async with aiosqlite.connect(self.db_path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM users;")
            total_users = int((await cur.fetchone() or (0,))[0])

            cur = await conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1;")
            active_users = int((await cur.fetchone() or (0,))[0])

            cur = await conn.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-24 hours');"
            )
            new_users_24h = int((await cur.fetchone() or (0,))[0])

            cur = await conn.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days');"
            )
            new_users_7d = int((await cur.fetchone() or (0,))[0])

            cur = await conn.execute("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM downloads;")
            dl_row = await cur.fetchone() or (0, 0)
            total_downloads = int(dl_row[0])
            total_bytes = int(dl_row[1])

            cur = await conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE created_at >= datetime('now', '-24 hours');"
            )
            downloads_24h = int((await cur.fetchone() or (0,))[0])

            cur = await conn.execute(
                """
                SELECT platform, COUNT(*) AS cnt
                FROM downloads
                GROUP BY platform
                ORDER BY cnt DESC
                LIMIT 8;
                """
            )
            top_rows = await cur.fetchall()
            top_platforms = [(str(r[0]), int(r[1])) for r in top_rows]

        return BotStatistics(
            total_users=total_users,
            active_users=active_users,
            new_users_24h=new_users_24h,
            new_users_7d=new_users_7d,
            total_downloads=total_downloads,
            downloads_24h=downloads_24h,
            total_bytes=total_bytes,
            top_platforms=top_platforms,
        )


db = Database(settings.db_path)
