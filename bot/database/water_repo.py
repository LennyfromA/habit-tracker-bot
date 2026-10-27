"""Repository for daily water intake logs and hydration goals."""

from typing import Any, Dict, Optional
from bot.database.db import get_db


async def get_water_target(user_id: int) -> int:
    """Retrieve user's daily hydration target in milliliters (default 2000 ml)."""
    async with get_db() as db:
        query = "SELECT target_ml FROM water_goals WHERE user_id = ?;"
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return int(row["target_ml"])
    return 2000


async def set_water_target(user_id: int, target_ml: int) -> int:
    """Set or update user's daily water target."""
    clean_target = max(500, min(10000, target_ml))
    async with get_db() as db:
        query = """
        INSERT INTO water_goals (user_id, target_ml, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            target_ml = excluded.target_ml,
            updated_at = CURRENT_TIMESTAMP;
        """
        await db.execute(query, (user_id, clean_target))
        await db.commit()
        return clean_target


async def add_water(user_id: int, logged_date: str, amount_ml: int) -> int:
    """Record a water intake entry and return updated total for the date."""
    clean_amount = max(1, amount_ml)
    async with get_db() as db:
        query = """
        INSERT INTO water_logs (user_id, logged_date, amount_ml)
        VALUES (?, ?, ?);
        """
        await db.execute(query, (user_id, logged_date, clean_amount))
        await db.commit()

    return await get_daily_water(user_id, logged_date)


async def get_daily_water(user_id: int, logged_date: str) -> int:
    """Calculate total milliliters drunk on the specified date."""
    async with get_db() as db:
        query = """
        SELECT COALESCE(SUM(amount_ml), 0) AS total_ml
        FROM water_logs
        WHERE user_id = ? AND logged_date = ?;
        """
        async with db.execute(query, (user_id, logged_date)) as cursor:
            row = await cursor.fetchone()
            return int(row["total_ml"]) if row else 0


async def undo_last_water(user_id: int, logged_date: str) -> Optional[int]:
    """Delete the most recent water intake entry for today. Returns removed ml or None."""
    async with get_db() as db:
        find_query = """
        SELECT id, amount_ml FROM water_logs
        WHERE user_id = ? AND logged_date = ?
        ORDER BY id DESC LIMIT 1;
        """
        async with db.execute(find_query, (user_id, logged_date)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            entry_id = row["id"]
            amount = row["amount_ml"]

        del_query = "DELETE FROM water_logs WHERE id = ?;"
        await db.execute(del_query, (entry_id,))
        await db.commit()
        return int(amount)


async def reset_daily_water(user_id: int, logged_date: str) -> None:
    """Clear all water records for the specified user and date."""
    async with get_db() as db:
        query = "DELETE FROM water_logs WHERE user_id = ? AND logged_date = ?;"
        await db.execute(query, (user_id, logged_date))
        await db.commit()


async def get_weekly_water_stats(user_id: int, start_date: str, end_date: str) -> Dict[str, int]:
    """Return dictionary of {logged_date: total_ml} over date range."""
    async with get_db() as db:
        query = """
        SELECT logged_date, COALESCE(SUM(amount_ml), 0) AS daily_ml
        FROM water_logs
        WHERE user_id = ? AND logged_date BETWEEN ? AND ?
        GROUP BY logged_date
        ORDER BY logged_date ASC;
        """
        async with db.execute(query, (user_id, start_date, end_date)) as cursor:
            rows = await cursor.fetchall()
            return {r["logged_date"]: int(r["daily_ml"]) for r in rows}
