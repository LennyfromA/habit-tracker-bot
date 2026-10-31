"""Repository for habit definitions, daily progress logs, and streak tracking."""

import time
from typing import Any, Dict, List, Optional, Tuple

from bot.database.db import get_db
from bot.database import users_repo


async def create_habit(
    user_id: int,
    name: str,
    target_minutes: int = 30,
    active_days: str = "1,2,3,4,5,6,7",
    habit_type: str = "duration",
    target_repeats: int = 1,
    reminder_times: Optional[str] = None,
) -> int:
    """Create a new habit and return its ID."""
    async with get_db() as db:
        query = """
        INSERT INTO habits (user_id, name, target_minutes, active_days, streak_days, is_frozen_today, habit_type, target_repeats, reminder_times)
        VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?);
        """
        cursor = await db.execute(
            query,
            (user_id, name.strip(), max(0, target_minutes), active_days, habit_type, max(1, target_repeats), reminder_times),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def get_habit_by_id(habit_id: int) -> Optional[Dict[str, Any]]:
    """Fetch habit by its primary key ID."""
    async with get_db() as db:
        query = "SELECT * FROM habits WHERE id = ?;"
        async with db.execute(query, (habit_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_habit(
    habit_id: int,
    user_id: int,
    name: Optional[str] = None,
    target_minutes: Optional[int] = None,
    active_days: Optional[str] = None,
    habit_type: Optional[str] = None,
    target_repeats: Optional[int] = None,
    reminder_times: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update habit fields verifying user ownership."""
    fields = []
    params = []
    if name is not None:
        fields.append("name = ?")
        params.append(name.strip())
    if target_minutes is not None:
        fields.append("target_minutes = ?")
        params.append(max(0, target_minutes))
    if active_days is not None:
        fields.append("active_days = ?")
        params.append(active_days.strip())
    if habit_type is not None:
        fields.append("habit_type = ?")
        params.append(habit_type.strip())
    if target_repeats is not None:
        fields.append("target_repeats = ?")
        params.append(max(1, target_repeats))
    if reminder_times is not None:
        fields.append("reminder_times = ?")
        params.append(reminder_times.strip() if reminder_times.strip() else None)

    if not fields:
        return await get_habit_by_id(habit_id)

    params.extend([habit_id, user_id])
    async with get_db() as db:
        query = f"""
        UPDATE habits
        SET {", ".join(fields)}
        WHERE id = ? AND user_id = ?
        RETURNING *;
        """
        async with db.execute(query, tuple(params)) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            return dict(row) if row else None


async def get_habits_for_user(user_id: int) -> List[Dict[str, Any]]:
    """Fetch all habits owned by the given user."""
    async with get_db() as db:
        query = "SELECT * FROM habits WHERE user_id = ? ORDER BY id ASC;"
        async with db.execute(query, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_today_habits_with_status(
    user_id: int,
    local_date: str,
    weekday: int,
) -> List[Dict[str, Any]]:
    """
    Fetch habits that are scheduled for the current weekday (1=Mon ... 7=Sun)
    joined with the habit_logs for the specified local date (YYYY-MM-DD).
    """
    async with get_db() as db:
        query = """
        SELECT
            h.id,
            h.user_id,
            h.name,
            h.target_minutes,
            h.active_days,
            h.streak_days,
            h.is_frozen_today,
            h.habit_type,
            h.target_repeats,
            h.reminder_times,
            h.created_at,
            COALESCE(l.spent_minutes, 0) AS spent_minutes,
            COALESCE(l.completed_repeats, 0) AS completed_repeats,
            COALESCE(l.is_completed, 0) AS is_completed,
            l.notes
        FROM habits h
        LEFT JOIN habit_logs l ON l.habit_id = h.id AND l.logged_date = ?
        WHERE h.user_id = ?
          AND (',' || h.active_days || ',') LIKE ('%,' || ? || ',%')
        ORDER BY h.id ASC;
        """
        async with db.execute(query, (local_date, user_id, str(weekday))) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_all_habits_with_today_status(
    user_id: int,
    local_date: str,
) -> List[Dict[str, Any]]:
    """Fetch all habits of user joined with today's log."""
    async with get_db() as db:
        query = """
        SELECT
            h.id,
            h.user_id,
            h.name,
            h.target_minutes,
            h.active_days,
            h.streak_days,
            h.is_frozen_today,
            h.habit_type,
            h.target_repeats,
            h.reminder_times,
            h.created_at,
            COALESCE(l.spent_minutes, 0) AS spent_minutes,
            COALESCE(l.completed_repeats, 0) AS completed_repeats,
            COALESCE(l.is_completed, 0) AS is_completed,
            l.notes
        FROM habits h
        LEFT JOIN habit_logs l ON l.habit_id = h.id AND l.logged_date = ?
        WHERE h.user_id = ?
        ORDER BY h.id ASC;
        """
        async with db.execute(query, (local_date, user_id)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_habit(habit_id: int, user_id: int) -> bool:
    """Delete a habit and cascaded records."""
    async with get_db() as db:
        query = "DELETE FROM habits WHERE id = ? AND user_id = ?;"
        cursor = await db.execute(query, (habit_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def freeze_habit_today(habit_id: int, user_id: int) -> Tuple[bool, str]:
    """
    Deduct 1 freeze from user and mark habit as is_frozen_today = 1.
    Returns (success, message).
    """
    habit = await get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        return False, "Привычка не найдена."

    if habit["is_frozen_today"]:
        return False, "Эта привычка уже заморожена на сегодня!"

    deducted = await users_repo.deduct_freeze(user_id)
    if not deducted:
        return False, "У вас не осталось заморозок на этой неделе. Они возобновятся в понедельник!"

    async with get_db() as db:
        query = "UPDATE habits SET is_frozen_today = 1 WHERE id = ?;"
        await db.execute(query, (habit_id,))
        await db.commit()

    user = await users_repo.get_user(user_id)
    freezes_left = user["freezes_left"] if user else 0
    return True, f"❄️ Привычка «{habit['name']}» заморожена на сегодня! Стрик в безопасности. Осталось заморозок: {freezes_left}."


async def upsert_habit_log(
    habit_id: int,
    user_id: int,
    logged_date: str,
    add_minutes: int,
    target_minutes: int,
) -> Tuple[int, bool]:
    """
    Increment spent_minutes for a habit on logged_date.
    Returns (total_spent_minutes, is_completed).
    """
    async with get_db() as db:
        query = """
        INSERT INTO habit_logs (habit_id, user_id, logged_date, spent_minutes, is_completed)
        VALUES (?, ?, ?, ?, CASE WHEN ? >= ? THEN 1 ELSE 0 END)
        ON CONFLICT(habit_id, logged_date) DO UPDATE SET
            spent_minutes = habit_logs.spent_minutes + excluded.spent_minutes,
            is_completed = CASE
                WHEN habit_logs.spent_minutes + excluded.spent_minutes >= ? THEN 1
                ELSE habit_logs.is_completed
            END
        RETURNING spent_minutes, is_completed;
        """
        async with db.execute(
            query,
            (
                habit_id,
                user_id,
                logged_date,
                add_minutes,
                add_minutes,
                target_minutes,
                target_minutes,
            ),
        ) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            if row:
                return row["spent_minutes"], bool(row["is_completed"])
            return add_minutes, (add_minutes >= target_minutes)


async def increment_habit_repeat(
    habit_id: int,
    user_id: int,
    logged_date: str,
) -> Tuple[int, int, bool]:
    """
    Increment completed_repeats by 1 for a checklist habit.
    Returns (completed_repeats, target_repeats, is_completed).
    """
    habit = await get_habit_by_id(habit_id)
    if not habit:
        return 0, 1, False
    target_repeats = habit.get("target_repeats", 1) or 1

    async with get_db() as db:
        query = """
        INSERT INTO habit_logs (habit_id, user_id, logged_date, spent_minutes, completed_repeats, is_completed)
        VALUES (?, ?, ?, 0, 1, CASE WHEN 1 >= ? THEN 1 ELSE 0 END)
        ON CONFLICT(habit_id, logged_date) DO UPDATE SET
            completed_repeats = habit_logs.completed_repeats + 1,
            is_completed = CASE
                WHEN habit_logs.completed_repeats + 1 >= ? THEN 1
                ELSE habit_logs.is_completed
            END
        RETURNING completed_repeats, is_completed;
        """
        async with db.execute(query, (habit_id, user_id, logged_date, target_repeats, target_repeats)) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            if row:
                return row["completed_repeats"], target_repeats, bool(row["is_completed"])
            return 1, target_repeats, (1 >= target_repeats)


async def set_habit_completed_manual(
    habit_id: int,
    user_id: int,
    logged_date: str,
    target_minutes: int = 0,
) -> None:
    """Manually complete a habit (e.g. from evening check-in)."""
    habit = await get_habit_by_id(habit_id)
    target_reps = habit.get("target_repeats", 1) if habit else 1
    tgt_mins = habit.get("target_minutes", target_minutes) if habit else target_minutes

    async with get_db() as db:
        query = """
        INSERT INTO habit_logs (habit_id, user_id, logged_date, spent_minutes, completed_repeats, is_completed)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(habit_id, logged_date) DO UPDATE SET
            spent_minutes = CASE
                WHEN habit_logs.spent_minutes < excluded.spent_minutes THEN excluded.spent_minutes
                ELSE habit_logs.spent_minutes
            END,
            completed_repeats = CASE
                WHEN habit_logs.completed_repeats < excluded.completed_repeats THEN excluded.completed_repeats
                ELSE habit_logs.completed_repeats
            END,
            is_completed = 1;
        """
        await db.execute(query, (habit_id, user_id, logged_date, tgt_mins, target_reps))
        await db.commit()


async def add_habit_snooze(habit_id: int, user_id: int, snooze_seconds: int = 900) -> None:
    """Snooze a habit reminder by given seconds (default 15 minutes)."""
    now_ts = int(time.time())
    snooze_until = now_ts + snooze_seconds
    async with get_db() as db:
        await db.execute(
            "INSERT INTO habit_snoozes (habit_id, user_id, snooze_until) VALUES (?, ?, ?);",
            (habit_id, user_id, snooze_until),
        )
        await db.commit()


async def get_due_snoozes(now_ts: int) -> List[Dict[str, Any]]:
    """Fetch snoozed habit reminders that have expired and are due."""
    async with get_db() as db:
        query = """
        SELECT s.id AS snooze_id, s.habit_id, s.user_id, h.name, h.habit_type, h.target_repeats
        FROM habit_snoozes s
        JOIN habits h ON h.id = s.habit_id
        WHERE s.snooze_until <= ?;
        """
        async with db.execute(query, (now_ts,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_snooze(snooze_id: int) -> None:
    """Delete a processed snooze record."""
    async with get_db() as db:
        await db.execute("DELETE FROM habit_snoozes WHERE id = ?;", (snooze_id,))
        await db.commit()


async def record_habit_reminder_sent(habit_id: int, user_id: int, reminded_date: str, reminder_time: str) -> None:
    """Log that a reminder was sent for a habit at a specific time today."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO habit_reminders_log (habit_id, user_id, reminded_date, reminder_time)
            VALUES (?, ?, ?, ?);
            """,
            (habit_id, user_id, reminded_date, reminder_time),
        )
        await db.commit()


async def was_habit_reminder_sent(habit_id: int, reminded_date: str, reminder_time: str) -> bool:
    """Check if a reminder has already been sent today at this time."""
    async with get_db() as db:
        query = "SELECT id FROM habit_reminders_log WHERE habit_id = ? AND reminded_date = ? AND reminder_time = ?;"
        async with db.execute(query, (habit_id, reminded_date, reminder_time)) as cursor:
            row = await cursor.fetchone()
            return bool(row)


async def update_habit_log_notes(habit_id: int, logged_date: str, note: str) -> None:
    """Append or set reflection note for a habit log entry."""
    async with get_db() as db:
        # Check existing notes
        fetch_query = "SELECT notes FROM habit_logs WHERE habit_id = ? AND logged_date = ?;"
        async with db.execute(fetch_query, (habit_id, logged_date)) as cursor:
            row = await cursor.fetchone()
            existing_note = row["notes"] if row and row["notes"] else ""

        new_notes = f"{existing_note}; {note.strip()}".strip("; ") if existing_note else note.strip()
        update_query = "UPDATE habit_logs SET notes = ? WHERE habit_id = ? AND logged_date = ?;"
        await db.execute(update_query, (new_notes, habit_id, logged_date))
        await db.commit()


async def get_weekly_stats(
    user_id: int,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """
    Retrieve aggregated data for stats and charts between start_date and end_date (inclusive).
    """
    async with get_db() as db:
        # Daily focus minutes
        daily_query = """
        SELECT logged_date, SUM(spent_minutes) AS total_minutes
        FROM habit_logs
        WHERE user_id = ? AND logged_date BETWEEN ? AND ?
        GROUP BY logged_date
        ORDER BY logged_date ASC;
        """
        daily_data = {}
        async with db.execute(daily_query, (user_id, start_date, end_date)) as cursor:
            for r in await cursor.fetchall():
                daily_data[r["logged_date"]] = r["total_minutes"]

        # Per habit stats
        habits_query = """
        SELECT
            h.id,
            h.name,
            h.target_minutes,
            h.streak_days,
            h.active_days,
            h.habit_type,
            h.target_repeats,
            COALESCE(SUM(l.spent_minutes), 0) AS total_spent,
            COALESCE(SUM(l.completed_repeats), 0) AS total_repeats,
            COALESCE(SUM(CASE WHEN l.is_completed = 1 THEN 1 ELSE 0 END), 0) AS completed_days
        FROM habits h
        LEFT JOIN habit_logs l ON l.habit_id = h.id AND l.logged_date BETWEEN ? AND ?
        WHERE h.user_id = ?
        GROUP BY h.id
        ORDER BY h.id ASC;
        """
        habits_data = []
        async with db.execute(habits_query, (start_date, end_date, user_id)) as cursor:
            for r in await cursor.fetchall():
                habits_data.append(dict(r))

        return {
            "daily": daily_data,
            "habits": habits_data,
        }


async def get_export_data(
    user_id: int,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Retrieve all habits and daily logs for markdown export."""
    habits = await get_habits_for_user(user_id)
    result = []
    async with get_db() as db:
        for habit in habits:
            query = """
            SELECT logged_date, spent_minutes, completed_repeats, is_completed, notes
            FROM habit_logs
            WHERE habit_id = ? AND logged_date BETWEEN ? AND ?
            ORDER BY logged_date ASC;
            """
            async with db.execute(query, (habit["id"], start_date, end_date)) as cursor:
                logs = [dict(r) for r in await cursor.fetchall()]

            result.append({
                "habit": habit,
                "logs": logs,
            })
    return result


async def midnight_process_streaks_for_user(
    user_id: int,
    yesterday_date: str,
    yesterday_weekday: int,
) -> None:
    """
    Update streaks at user local midnight:
    - If habit was scheduled for yesterday:
      - If completed OR frozen: streak_days += 1
      - Else: streak_days = 0
    - Reset is_frozen_today = 0
    """
    async with get_db() as db:
        # Fetch user habits
        query = "SELECT * FROM habits WHERE user_id = ?;"
        async with db.execute(query, (user_id,)) as cursor:
            habits = [dict(r) for r in await cursor.fetchall()]

        for habit in habits:
            active_days_list = habit["active_days"].split(",")
            is_scheduled_yesterday = str(yesterday_weekday) in active_days_list

            if is_scheduled_yesterday:
                # Check log for yesterday
                log_q = "SELECT is_completed FROM habit_logs WHERE habit_id = ? AND logged_date = ?;"
                async with db.execute(log_q, (habit["id"], yesterday_date)) as log_cursor:
                    log_row = await log_cursor.fetchone()
                    was_completed = bool(log_row["is_completed"]) if log_row else False

                was_frozen = bool(habit["is_frozen_today"])
                if was_completed or was_frozen:
                    new_streak = habit["streak_days"] + 1
                else:
                    new_streak = 0

                await db.execute(
                    "UPDATE habits SET streak_days = ?, is_frozen_today = 0 WHERE id = ?;",
                    (new_streak, habit["id"]),
                )
            else:
                # If habit wasn't scheduled yesterday, preserve streak and just reset is_frozen_today
                await db.execute(
                    "UPDATE habits SET is_frozen_today = 0 WHERE id = ?;",
                    (habit["id"],),
                )

        await db.commit()
