"""Repository for weight logs, morning weight tracking, and weight statistics."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from bot.database.db import get_db


async def log_weight(
    user_id: int,
    logged_date: str,
    weight: float,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Log or update weight measurement for the specified date."""
    clean_weight = round(float(weight), 2)
    async with get_db() as db:
        query = """
        INSERT INTO weight_logs (user_id, logged_date, weight, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, logged_date) DO UPDATE SET
            weight = excluded.weight,
            note = excluded.note,
            created_at = CURRENT_TIMESTAMP
        RETURNING *;
        """
        async with db.execute(query, (user_id, logged_date, clean_weight, note)) as cursor:
            row = await cursor.fetchone()
            await db.commit()
            return dict(row) if row else {}


async def get_weight_for_date(user_id: int, logged_date: str) -> Optional[Dict[str, Any]]:
    """Retrieve weight measurement for a specific date."""
    async with get_db() as db:
        query = "SELECT * FROM weight_logs WHERE user_id = ? AND logged_date = ?;"
        async with db.execute(query, (user_id, logged_date)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_recent_weights(user_id: int, limit: int = 7) -> List[Dict[str, Any]]:
    """Retrieve the most recent weight entries ordered from latest to oldest."""
    async with get_db() as db:
        query = """
        SELECT * FROM weight_logs
        WHERE user_id = ?
        ORDER BY logged_date DESC
        LIMIT ?;
        """
        async with db.execute(query, (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_weight(user_id: int, logged_date: str) -> bool:
    """Delete weight entry for a specific date."""
    async with get_db() as db:
        query = "DELETE FROM weight_logs WHERE user_id = ? AND logged_date = ?;"
        cursor = await db.execute(query, (user_id, logged_date))
        await db.commit()
        return cursor.rowcount > 0


async def get_weight_stats(user_id: int, local_date: str) -> Dict[str, Any]:
    """
    Calculate weight statistics:
    - today's weight (if recorded)
    - latest weight and previous measurement
    - change since previous measurement and since 7 days ago
    - 30-day min and max
    """
    today_rec = await get_weight_for_date(user_id, local_date)
    recent = await get_recent_weights(user_id, limit=30)

    if not recent:
        return {
            "today_weight": None,
            "latest_weight": None,
            "previous_weight": None,
            "diff_from_previous": None,
            "diff_from_week_ago": None,
            "history": [],
            "count": 0,
        }

    latest = recent[0]["weight"]
    previous = recent[1]["weight"] if len(recent) > 1 else None
    diff_previous = round(latest - previous, 2) if previous is not None else None

    # Check for entry roughly 7 days ago (between 5 and 9 days ago)
    target_dt = datetime.strptime(local_date, "%Y-%m-%d")
    week_ago_weight = None
    for entry in recent:
        e_dt = datetime.strptime(entry["logged_date"], "%Y-%m-%d")
        delta_days = (target_dt - e_dt).days
        if 5 <= delta_days <= 9:
            week_ago_weight = entry["weight"]
            break

    diff_week = round(latest - week_ago_weight, 2) if week_ago_weight is not None else None

    weights_30d = [r["weight"] for r in recent]
    return {
        "today_weight": today_rec["weight"] if today_rec else None,
        "latest_weight": latest,
        "previous_weight": previous,
        "diff_from_previous": diff_previous,
        "diff_from_week_ago": diff_week,
        "min_30d": min(weights_30d),
        "max_30d": max(weights_30d),
        "history": recent[:7],
        "count": len(recent),
    }
