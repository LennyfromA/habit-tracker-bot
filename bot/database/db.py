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
    habit_id INTEGER DEFAULT NULL,
    task_id INTEGER DEFAULT NULL,
    started_at INTEGER NOT NULL, -- Unix timestamp (epoch seconds)
    total_target_seconds INTEGER NOT NULL,
    elapsed_before_pause INTEGER NOT NULL DEFAULT 0,
    is_paused INTEGER NOT NULL DEFAULT 0,
    message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    CHECK((habit_id IS NOT NULL AND task_id IS NULL) OR (habit_id IS NULL AND task_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS nutrition_goals (
    user_id INTEGER PRIMARY KEY,
    target_calories INTEGER NOT NULL DEFAULT 2000,
    target_protein REAL NOT NULL DEFAULT 0.0,
    target_fat REAL NOT NULL DEFAULT 0.0,
    target_carbs REAL NOT NULL DEFAULT 0.0,
    track_bju INTEGER NOT NULL DEFAULT 1,
    track_water INTEGER NOT NULL DEFAULT 1,
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

CREATE TABLE IF NOT EXISTS food_database (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    aliases TEXT NOT NULL,
    calories_100g REAL NOT NULL,
    protein_100g REAL NOT NULL DEFAULT 0.0,
    fat_100g REAL NOT NULL DEFAULT 0.0,
    carbs_100g REAL NOT NULL DEFAULT 0.0,
    portions_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS widget_messages (
    wid TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    view_type TEXT NOT NULL DEFAULT 'dashboard',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '📁',
    is_inbox INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    list_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    notes TEXT DEFAULT NULL,
    due_date TEXT DEFAULT NULL,                  -- YYYY-MM-DD (local user date)
    due_time TEXT DEFAULT NULL,                  -- HH:MM (optional reminder time)
    recurrence TEXT NOT NULL DEFAULT 'none',     -- 'none', 'daily', 'weekdays', 'weekly'
    estimated_minutes INTEGER DEFAULT NULL,      -- optional focus estimate
    spent_minutes INTEGER NOT NULL DEFAULT 0,    -- accumulated focus time
    is_completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT DEFAULT NULL,              -- YYYY-MM-DD (local user date)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY(list_id) REFERENCES task_lists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    is_completed INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_reminders_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reminded_date TEXT NOT NULL,
    reminder_time TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    UNIQUE(task_id, reminded_date, reminder_time)
);

CREATE INDEX IF NOT EXISTS idx_widget_messages_user ON widget_messages(user_id, view_type);
CREATE INDEX IF NOT EXISTS idx_task_lists_user ON task_lists(user_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_tasks_user_due ON tasks(user_id, is_completed, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_user_list ON tasks(user_id, list_id, is_completed);
CREATE INDEX IF NOT EXISTS idx_tasks_completed_at ON tasks(user_id, completed_at);
CREATE INDEX IF NOT EXISTS idx_task_subtasks_task ON task_subtasks(task_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_habits_user_id ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_habit_logs_user_date ON habit_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_habit_logs_habit_date ON habit_logs(habit_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_meal_logs_user_date ON meal_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_water_logs_user_date ON water_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_weight_logs_user_date ON weight_logs(user_id, logged_date);
CREATE INDEX IF NOT EXISTS idx_food_name ON food_database(name);
CREATE INDEX IF NOT EXISTS idx_food_category ON food_database(category);
"""


@asynccontextmanager
async def get_db(db_path: Optional[str] = None) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager for SQLite database connection."""
    target_path = db_path or settings.DB_PATH
    async with aiosqlite.connect(target_path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        db.row_factory = aiosqlite.Row
        yield db


async def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database tables and run safe column migrations."""
    target_path = db_path or settings.DB_PATH
    logger.info("Initializing database at: %s", target_path)
    async with aiosqlite.connect(target_path) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA journal_mode = WAL;")
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

        # Normalize malformed weight_reminder_time (e.g. '07' or '7' -> '07:00')
        await db.execute("""
            UPDATE users 
            SET weight_reminder_time = printf('%02d:00', CAST(weight_reminder_time AS INTEGER))
            WHERE weight_reminder_time NOT LIKE '%:%' AND length(trim(weight_reminder_time)) > 0;
        """)

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

        async with db.execute("PRAGMA table_info(nutrition_goals);") as cursor:
            cols = [r["name"] for r in await cursor.fetchall()]
            if "track_bju" not in cols:
                await db.execute("ALTER TABLE nutrition_goals ADD COLUMN track_bju INTEGER NOT NULL DEFAULT 1;")
            if "track_water" not in cols:
                await db.execute("ALTER TABLE nutrition_goals ADD COLUMN track_water INTEGER NOT NULL DEFAULT 1;")

        # Safe migration for active_timers to support both habits and tasks
        async with db.execute("PRAGMA table_info(active_timers);") as cursor:
            timer_info = {r["name"]: dict(r) for r in await cursor.fetchall()}
            needs_timer_migration = (
                "task_id" not in timer_info
                or timer_info.get("habit_id", {}).get("notnull") == 1
            )

        if needs_timer_migration:
            logger.info("Migrating active_timers table to support both habits and tasks...")
            await db.execute("PRAGMA foreign_keys = OFF;")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS active_timers_new (
                    user_id INTEGER PRIMARY KEY,
                    habit_id INTEGER DEFAULT NULL,
                    task_id INTEGER DEFAULT NULL,
                    started_at INTEGER NOT NULL,
                    total_target_seconds INTEGER NOT NULL,
                    elapsed_before_pause INTEGER NOT NULL DEFAULT 0,
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    CHECK((habit_id IS NOT NULL AND task_id IS NULL) OR (habit_id IS NULL AND task_id IS NOT NULL))
                );
            """)
            await db.execute("""
                INSERT OR REPLACE INTO active_timers_new (
                    user_id, habit_id, task_id, started_at, total_target_seconds,
                    elapsed_before_pause, is_paused, message_id, chat_id
                )
                SELECT
                    user_id, habit_id, NULL, started_at, total_target_seconds,
                    elapsed_before_pause, is_paused, message_id, chat_id
                FROM active_timers;
            """)
            await db.execute("DROP TABLE active_timers;")
            await db.execute("ALTER TABLE active_timers_new RENAME TO active_timers;")
            await db.execute("PRAGMA foreign_keys = ON;")

        await db.commit()

    # Seed food catalog if table is empty
    from bot.database.food_repo import seed_food_catalog_if_empty
    await seed_food_catalog_if_empty(target_path)

    logger.info("Database initialized successfully.")
