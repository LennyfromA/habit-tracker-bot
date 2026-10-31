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
    weight_reminder_enabled INTEGER NOT NULL DEFAULT 0, -- 1=enabled, 0=disabled
    weight_reminder_time TEXT NOT NULL DEFAULT '08:00', -- HH:MM
    last_weight_reminder_date TEXT DEFAULT NULL,
    notifications_muted_until INTEGER NOT NULL DEFAULT 0, -- Unix timestamp until which notifications are muted
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    target_minutes INTEGER NOT NULL, -- Target time per active day (0 for checklist habits)
    active_days TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7', -- 1=Mon, 7=Sun (e.g. '1,2,3,4,5' for weekdays)
    streak_days INTEGER NOT NULL DEFAULT 0,
    is_frozen_today INTEGER NOT NULL DEFAULT 0, -- 1 if frozen today
    habit_type TEXT NOT NULL DEFAULT 'duration', -- 'duration' (minutes) or 'checklist' (repeats/checkbox)
    target_repeats INTEGER NOT NULL DEFAULT 1, -- Times per day for checklist habits
    reminder_times TEXT DEFAULT NULL, -- Comma-separated HH:MM, e.g. '09:00,21:00'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS habit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    logged_date TEXT NOT NULL, -- YYYY-MM-DD (local user date)
    spent_minutes INTEGER NOT NULL DEFAULT 0,
    completed_repeats INTEGER NOT NULL DEFAULT 0,
    is_completed INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT NULL,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
    UNIQUE(habit_id, logged_date)
);

CREATE TABLE IF NOT EXISTS habit_reminders_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reminded_date TEXT NOT NULL,
    reminder_time TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
    UNIQUE(habit_id, reminded_date, reminder_time)
);

CREATE TABLE IF NOT EXISTS habit_snoozes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    habit_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    snooze_until INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS nutrition_goals (
    user_id INTEGER PRIMARY KEY,
    target_calories INTEGER NOT NULL DEFAULT 2000,
    target_protein REAL NOT NULL DEFAULT 0.0,
    target_fat REAL NOT NULL DEFAULT 0.0,
    target_carbs REAL NOT NULL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meal_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    logged_date TEXT NOT NULL,                  -- YYYY-MM-DD
    meal_type TEXT NOT NULL DEFAULT 'snack',    -- breakfast, lunch, dinner, snack
    title TEXT NOT NULL,
    calories INTEGER NOT NULL,
    protein REAL NOT NULL DEFAULT 0.0,
    fat REAL NOT NULL DEFAULT 0.0,
    carbs REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS water_goals (
    user_id INTEGER PRIMARY KEY,
    target_ml INTEGER NOT NULL DEFAULT 2000,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS water_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    logged_date TEXT NOT NULL,                  -- YYYY-MM-DD
    amount_ml INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS weight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    logged_date TEXT NOT NULL,                  -- YYYY-MM-DD
    weight REAL NOT NULL,                       -- e.g. 72.4
    note TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, logged_date)
);

CREATE INDEX IF NOT EXISTS idx_habits_user_id ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_user_date ON habit_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_date ON habit_logs(habit_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_meal_logs_user_date ON meal_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_water_logs_user_date ON water_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_weight_logs_user_date ON weight_logs(user_id, logged_date);
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
    """Initialize database tables and run safe column migrations."""
    target_path = db_path or settings.DB_PATH
    logger.info("Initializing database at: %s", target_path)
    async with aiosqlite.connect(target_path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(INIT_SQL)

        # Safe column migrations for existing databases
        async with db.execute("PRAGMA table_info(users);") as cursor:
            cols = [r["name"] for r in await cursor.fetchall()]
            if "weight_reminder_enabled" not in cols:
                await db.execute("ALTER TABLE users ADD COLUMN weight_reminder_enabled INTEGER NOT NULL DEFAULT 0;")
            if "weight_reminder_time" not in cols:
                await db.execute("ALTER TABLE users ADD COLUMN weight_reminder_time TEXT NOT NULL DEFAULT '08:00';")
            if "last_weight_reminder_date" not in cols:
                await db.execute("ALTER TABLE users ADD COLUMN last_weight_reminder_date TEXT DEFAULT NULL;")
            if "notifications_muted_until" not in cols:
                await db.execute("ALTER TABLE users ADD COLUMN notifications_muted_until INTEGER NOT NULL DEFAULT 0;")

        async with db.execute("PRAGMA table_info(habits);") as cursor:
            cols = [r["name"] for r in await cursor.fetchall()]
            if "habit_type" not in cols:
                await db.execute("ALTER TABLE habits ADD COLUMN habit_type TEXT NOT NULL DEFAULT 'duration';")
            if "target_repeats" not in cols:
                await db.execute("ALTER TABLE habits ADD COLUMN target_repeats INTEGER NOT NULL DEFAULT 1;")
            if "reminder_times" not in cols:
                await db.execute("ALTER TABLE habits ADD COLUMN reminder_times TEXT DEFAULT NULL;")

        async with db.execute("PRAGMA table_info(habit_logs);") as cursor:
            cols = [r["name"] for r in await cursor.fetchall()]
            if "completed_repeats" not in cols:
                await db.execute("ALTER TABLE habit_logs ADD COLUMN completed_repeats INTEGER NOT NULL DEFAULT 0;")

        await db.commit()
    logger.info("Database initialized successfully.")
