"""Repository for user management and settings queries."""

from typing import Any, Dict, List, Optional

from bot.database.db import get_db


async def create_or_update_user(
    user_id: int,
    username: Optional[str] = None,
    utc_offset: int = 3,
    checkin_time: str = "21:00",
) -> Dict[str, Any]:
    """Create a new user or update basic info if already registered."""
    async with get_db() as db:
        query = """
        INSERT INTO users (user_id, username, utc_offset, checkin_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = COALESCE(excluded.username, users.username)
        RETURNING *;
        """
        async with db.execute(query, (user_id, username, utc_offset, checkin_time)) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            return dict(row) if row else {}


async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve user record by user_id."""
    async with get_db() as db:
        query = "SELECT * FROM users WHERE user_id = ?;"
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_user_timezone(user_id: int, utc_offset: int) -> None:
    """Update user's UTC offset (in hours)."""
    async with get_db() as db:
        query = "UPDATE users SET utc_offset = ? WHERE user_id = ?;"
        await db.execute(query, (utc_offset, user_id))
        await db.commit()


async def update_user_checkin_time(user_id: int, checkin_time: str) -> None:
    """Update user's daily check-in time (HH:MM)."""
    async with get_db() as db:
        query = "UPDATE users SET checkin_time = ? WHERE user_id = ?;"
        await db.execute(query, (checkin_time, user_id))
        await db.commit()


async def update_user_last_checkin_date(user_id: int, checkin_date: str) -> None:
    """Store date string when check-in notification was sent to avoid duplicate triggers."""
    async with get_db() as db:
        query = "UPDATE users SET last_checkin_date = ? WHERE user_id = ?;"
        await db.execute(query, (checkin_date, user_id))
        await db.commit()


async def update_user_last_midnight_date(user_id: int, midnight_date: str) -> None:
    """Store date string when midnight reset was performed."""
    async with get_db() as db:
        query = "UPDATE users SET last_midnight_date = ? WHERE user_id = ?;"
        await db.execute(query, (midnight_date, user_id))
        await db.commit()


async def deduct_freeze(user_id: int) -> bool:
    """
    Deduct 1 freeze from user's freezes_left if available.
    Returns True if deduction succeeded, False otherwise.
    """
    async with get_db() as db:
        query = """
        UPDATE users
        SET freezes_left = freezes_left - 1
        WHERE user_id = ? AND freezes_left > 0;
        """
        cursor = await db.execute(query, (user_id,))
        await db.commit()
        return cursor.rowcount > 0


async def reset_weekly_freezes(user_id: Optional[int] = None) -> None:
    """Reset freezes_left to freezes_per_week."""
    async with get_db() as db:
        if user_id is not None:
            query = "UPDATE users SET freezes_left = freezes_per_week WHERE user_id = ?;"
            await db.execute(query, (user_id,))
        else:
            query = "UPDATE users SET freezes_left = freezes_per_week;"
            await db.execute(query)
        await db.commit()


async def get_all_users() -> List[Dict[str, Any]]:
    """Retrieve all users."""
    async with get_db() as db:
        query = "SELECT * FROM users;"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
