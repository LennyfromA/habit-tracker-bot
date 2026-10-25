"""Database connection management and initialization scripts."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import aiosqlite

from bot.config import settings

logger = logging.getLogger(__name__)

INIT_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    utc_offset INTEGER NOT NULL DEFAULT 3, -- in hours (-12 to +14)
    checkin_time TEXT NOT NULL DEFAULT '21:00', -- HH:MM
    freezes_per_week INTEGER NOT NULL DEFAULT 1,
    freezes_left INTEGER NOT NULL DEFAULT 1,
    last_checkin_date TEXT DEFAULT NULL,
    last_midnight_date TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    target_minutes INTEGER NOT NULL, -- Target time per active day
    active_days TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7', -- 1=Mon, 7=Sun (e.g. '1,2,3,4,5' for weekdays)
    streak_days INTEGER NOT NULL DEFAULT 0,
    is_frozen_today INTEGER NOT NULL DEFAULT 0, -- 1 if frozen today
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    logged_date TEXT NOT NULL, -- YYYY-MM-DD (local user date)
    spent_minutes INTEGER NOT NULL DEFAULT 0,
    is_completed INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT NULL,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
    UNIQUE(habit_id, logged_date)
);

CREATE TABLE IF NOT EXISTS active_timers (
    user_id INTEGER PRIMARY KEY,
    habit_id INTEGER NOT NULL,
    started_at INTEGER NOT NULL, -- Unix timestamp (epoch seconds)
    total_target_seconds INTEGER NOT NULL,
    elapsed_before_pause INTEGER NOT NULL DEFAULT 0,
    is_paused INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_habits_user_id ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_user_date ON habit_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_date ON habit_logs(habit_id, logged_date);
"""


@asynccontextmanager
async def get_db(db_path: Optional[str] = None) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager for SQLite database connection."""
    target_path = db_path or settings.DB_PATH
    async with aiosqlite.connect(target_path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA journal_mode = WAL;")
        db.row_factory = aiosqlite.Row
        yield db


async def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database tables and indexes."""
    target_path = db_path or settings.DB_PATH
    logger.info("Initializing database at: %s", target_path)
    async with aiosqlite.connect(target_path) as db:
        await db.executescript(INIT_SQL)
        await db.commit()
    logger.info("Database initialized successfully.")
