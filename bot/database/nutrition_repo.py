"""Repository for nutrition goals, meal logging, and daily intake statistics."""

from typing import Any, Dict, List, Optional
from bot.database.db import get_db


async def get_nutrition_goals(user_id: int) -> Dict[str, Any]:
    """Retrieve user's nutrition goals, returning sensible defaults if not configured."""
    async with get_db() as db:
        query = "SELECT * FROM nutrition_goals WHERE user_id = ?;"
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)

    return {
        "user_id": user_id,
        "target_calories": 2000,
        "target_protein": 0.0,
        "target_fat": 0.0,
        "target_carbs": 0.0,
    }


async def set_nutrition_goals(
    user_id: int,
    calories: int,
    protein: float = 0.0,
    fat: float = 0.0,
    carbs: float = 0.0,
) -> Dict[str, Any]:
    """Create or update daily nutrition target."""
    async with get_db() as db:
        query = """
        INSERT INTO nutrition_goals (user_id, target_calories, target_protein, target_fat, target_carbs, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            target_calories = excluded.target_calories,
            target_protein = excluded.target_protein,
            target_fat = excluded.target_fat,
            target_carbs = excluded.target_carbs,
            updated_at = CURRENT_TIMESTAMP
        RETURNING *;
        """
        async with db.execute(query, (user_id, calories, protein, fat, carbs)) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            return dict(row) if row else {}


async def add_meal(
    user_id: int,
    logged_date: str,
    title: str,
    calories: int,
    protein: float = 0.0,
    fat: float = 0.0,
    carbs: float = 0.0,
    meal_type: str = "snack",
) -> Dict[str, Any]:
    """Record a meal or snack in daily log."""
    clean_title = title.strip() or "Перекус"
    clean_type = meal_type.strip().lower() if meal_type else "snack"

    async with get_db() as db:
        query = """
        INSERT INTO meal_logs (user_id, logged_date, meal_type, title, calories, protein, fat, carbs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING *;
        """
        async with db.execute(
            query,
            (user_id, logged_date, clean_type, clean_title, max(0, calories), max(0.0, protein), max(0.0, fat), max(0.0, carbs)),
        ) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            return dict(row) if row else {}


async def get_meals_for_date(user_id: int, logged_date: str) -> List[Dict[str, Any]]:
    """Fetch all meals logged by user for a given local date."""
    async with get_db() as db:
        query = """
        SELECT * FROM meal_logs
        WHERE user_id = ? AND logged_date = ?
        ORDER BY id ASC;
        """
        async with db.execute(query, (user_id, logged_date)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_meal_by_id(meal_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve a single meal by ID verifying user ownership."""
    async with get_db() as db:
        query = "SELECT * FROM meal_logs WHERE id = ? AND user_id = ?;"
        async with db.execute(query, (meal_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def delete_meal(meal_id: int, user_id: int) -> bool:
    """Delete a meal log entry owned by user. Returns True if row was deleted."""
    async with get_db() as db:
        query = "DELETE FROM meal_logs WHERE id = ? AND user_id = ?;"
        cursor = await db.execute(query, (meal_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_daily_summary(user_id: int, logged_date: str) -> Dict[str, Any]:
    """Calculate aggregated calories and macro totals for a specific date."""
    async with get_db() as db:
        query = """
        SELECT
            COALESCE(SUM(calories), 0) AS total_calories,
            ROUND(COALESCE(SUM(protein), 0.0), 1) AS total_protein,
            ROUND(COALESCE(SUM(fat), 0.0), 1) AS total_fat,
            ROUND(COALESCE(SUM(carbs), 0.0), 1) AS total_carbs,
            COUNT(id) AS meal_count
        FROM meal_logs
        WHERE user_id = ? AND logged_date = ?;
        """
        async with db.execute(query, (user_id, logged_date)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {
                "total_calories": 0,
                "total_protein": 0.0,
                "total_fat": 0.0,
                "total_carbs": 0.0,
                "meal_count": 0,
            }


async def get_weekly_nutrition_stats(
    user_id: int,
    start_date: str,
    end_date: str,
) -> Dict[str, int]:
    """Return dictionary of {logged_date: total_calories} for a date range."""
    async with get_db() as db:
        query = """
        SELECT logged_date, COALESCE(SUM(calories), 0) AS daily_cals
        FROM meal_logs
        WHERE user_id = ? AND logged_date BETWEEN ? AND ?
        GROUP BY logged_date
        ORDER BY logged_date ASC;
        """
        async with db.execute(query, (user_id, start_date, end_date)) as cursor:
            rows = await cursor.fetchall()
            return {r["logged_date"]: int(r["daily_cals"]) for r in rows}
