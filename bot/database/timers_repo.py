"""Repository for persisting and managing active focus sessions."""

import time
from typing import Any, Dict, List, Optional

from bot.database.db import get_db


async def create_or_replace_timer(
    user_id: int,
    habit_id: Optional[int] = None,
    total_target_seconds: int = 1500,
    message_id: int = 0,
    chat_id: int = 0,
    started_at: Optional[int] = None,
    task_id: Optional[int] = None,
) -> None:
    """Save a new active timer, replacing any previous timer for this user.
    Enforces XOR: exactly one of habit_id or task_id must be provided.
    """
    if isinstance(habit_id, dict):
        habit_id = habit_id.get("id")
    if isinstance(task_id, dict):
        task_id = task_id.get("id")

    if (habit_id is None and task_id is None) or (habit_id is not None and task_id is not None):
        raise ValueError("Exactly one of habit_id or task_id must be provided to create_or_replace_timer")

    start_ts = started_at if started_at is not None else int(time.time())
    async with get_db() as db:
        query = """
        INSERT INTO active_timers (
            user_id, habit_id, task_id, started_at, total_target_seconds,
            elapsed_before_pause, is_paused, message_id, chat_id
        )
        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            habit_id = excluded.habit_id,
            task_id = excluded.task_id,
            started_at = excluded.started_at,
            total_target_seconds = excluded.total_target_seconds,
            elapsed_before_pause = 0,
            is_paused = 0,
            message_id = excluded.message_id,
            chat_id = excluded.chat_id;
        """
        await db.execute(
            query,
            (user_id, habit_id, task_id, start_ts, total_target_seconds, message_id, chat_id),
        )
        await db.commit()


async def get_timer(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve active timer for a user including habit or task details."""
    async with get_db() as db:
        query = """
        SELECT
            t.user_id,
            t.habit_id,
            t.task_id,
            t.started_at,
            t.total_target_seconds,
            t.elapsed_before_pause,
            t.is_paused,
            t.message_id,
            t.chat_id,
            CASE WHEN t.habit_id IS NOT NULL THEN 'habit' ELSE 'task' END AS target_type,
            COALESCE(h.name, tk.title) AS target_name,
            COALESCE(h.name, tk.title) AS habit_name,
            COALESCE(h.target_minutes, tk.estimated_minutes, 25) AS target_minutes
        FROM active_timers t
        LEFT JOIN habits h ON h.id = t.habit_id
        LEFT JOIN tasks tk ON tk.id = t.task_id
        WHERE t.user_id = ?;
        """
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def pause_timer(user_id: int, now_ts: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Pause an active timer and update elapsed seconds."""
    current_ts = now_ts if now_ts is not None else int(time.time())
    timer = await get_timer(user_id)
    if not timer or timer["is_paused"]:
        return timer

    elapsed = timer["elapsed_before_pause"] + max(0, current_ts - timer["started_at"])
    async with get_db() as db:
        query = """
        UPDATE active_timers
        SET elapsed_before_pause = ?, is_paused = 1
        WHERE user_id = ?;
        """
        await db.execute(query, (elapsed, user_id))
        await db.commit()

    return await get_timer(user_id)


async def resume_timer(user_id: int, now_ts: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Resume a paused timer, resetting started_at to current timestamp."""
    current_ts = now_ts if now_ts is not None else int(time.time())
    timer = await get_timer(user_id)
    if not timer or not timer["is_paused"]:
        return timer

    async with get_db() as db:
        query = """
        UPDATE active_timers
        SET started_at = ?, is_paused = 0
        WHERE user_id = ?;
        """
        await db.execute(query, (current_ts, user_id))
        await db.commit()

    return await get_timer(user_id)


async def add_time_to_timer(
    user_id: int,
    additional_seconds: int = 600,
) -> Optional[Dict[str, Any]]:
    """Extend timer duration by adding seconds to total_target_seconds."""
    async with get_db() as db:
        query = """
        UPDATE active_timers
        SET total_target_seconds = total_target_seconds + ?
        WHERE user_id = ?;
        """
        await db.execute(query, (additional_seconds, user_id))
        await db.commit()

    return await get_timer(user_id)


async def delete_timer(user_id: int) -> Optional[Dict[str, Any]]:
    """Atomically delete and return active timer record."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE;")
        select_query = """
        SELECT
            t.user_id,
            t.habit_id,
            t.task_id,
            t.started_at,
            t.total_target_seconds,
            t.elapsed_before_pause,
            t.is_paused,
            t.message_id,
            t.chat_id,
            CASE WHEN t.habit_id IS NOT NULL THEN 'habit' ELSE 'task' END AS target_type,
            COALESCE(h.name, tk.title) AS target_name,
            COALESCE(h.name, tk.title) AS habit_name,
            COALESCE(h.target_minutes, tk.estimated_minutes, 25) AS target_minutes
        FROM active_timers t
        LEFT JOIN habits h ON h.id = t.habit_id
        LEFT JOIN tasks tk ON tk.id = t.task_id
        WHERE t.user_id = ?;
        """
        async with db.execute(select_query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.rollback()
                return None
            timer = dict(row)

        delete_cursor = await db.execute("DELETE FROM active_timers WHERE user_id = ?;", (user_id,))
        if delete_cursor.rowcount == 0:
            await db.rollback()
            return None

        await db.commit()
        return timer


async def get_all_running_timers() -> List[Dict[str, Any]]:
    """Retrieve all non-paused active timers across all users."""
    async with get_db() as db:
        query = """
        SELECT
            t.user_id,
            t.habit_id,
            t.task_id,
            t.started_at,
            t.total_target_seconds,
            t.elapsed_before_pause,
            t.is_paused,
            t.message_id,
            t.chat_id,
            CASE WHEN t.habit_id IS NOT NULL THEN 'habit' ELSE 'task' END AS target_type,
            COALESCE(h.name, tk.title) AS target_name,
            COALESCE(h.name, tk.title) AS habit_name,
            COALESCE(h.target_minutes, tk.estimated_minutes, 25) AS target_minutes
        FROM active_timers t
        LEFT JOIN habits h ON h.id = t.habit_id
        LEFT JOIN tasks tk ON tk.id = t.task_id
        WHERE t.is_paused = 0;
        """
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
