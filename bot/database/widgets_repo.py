"""Repository for widget-to-chat message bindings and synchronization metadata."""

import logging
from typing import Any, Dict, Optional

from bot.database.db import get_db

logger = logging.getLogger(__name__)


async def save_widget_message(
    wid: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    view_type: str = "dashboard",
    db_path: Optional[str] = None,
) -> None:
    """Save or update widget message reference for synchronization."""
    query = """
    INSERT INTO widget_messages (wid, user_id, chat_id, message_id, view_type, updated_at)
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(wid) DO UPDATE SET
        chat_id = excluded.chat_id,
        message_id = excluded.message_id,
        view_type = excluded.view_type,
        updated_at = CURRENT_TIMESTAMP;
    """
    async with get_db(db_path) as db:
        await db.execute(query, (wid, user_id, chat_id, message_id, view_type))
        await db.commit()


async def get_widget_message(
    wid: str,
    user_id: int,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve widget message reference with strict user ownership verification."""
    query = "SELECT * FROM widget_messages WHERE wid = ? AND user_id = ?;"
    async with get_db(db_path) as db:
        async with db.execute(query, (wid, user_id)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_latest_widget_message(
    user_id: int,
    view_type: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve the most recently updated widget message for a user, optionally by view_type."""
    if view_type:
        query = """
        SELECT * FROM widget_messages
        WHERE user_id = ? AND view_type = ?
        ORDER BY updated_at DESC, rowid DESC
        LIMIT 1;
        """
        params = (user_id, view_type)
    else:
        query = """
        SELECT * FROM widget_messages
        WHERE user_id = ?
        ORDER BY updated_at DESC, rowid DESC
        LIMIT 1;
        """
        params = (user_id,)

    async with get_db(db_path) as db:
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def touch_widget_message(wid: str, db_path: Optional[str] = None) -> None:
    """Update updated_at timestamp on active widget message."""
    query = "UPDATE widget_messages SET updated_at = CURRENT_TIMESTAMP WHERE wid = ?;"
    async with get_db(db_path) as db:
        await db.execute(query, (wid,))
        await db.commit()


async def cleanup_old_widget_messages(days: int = 30, db_path: Optional[str] = None) -> int:
    """Delete widget message records older than specified days."""
    query = "DELETE FROM widget_messages WHERE updated_at < datetime('now', ?);"
    offset_param = f"-{int(days)} days"
    async with get_db(db_path) as db:
        cursor = await db.execute(query, (offset_param,))
        await db.commit()
        deleted_count = cursor.rowcount if cursor.rowcount is not None else 0
        if deleted_count > 0:
            logger.info("Cleaned up %d old widget message records older than %d days.", deleted_count, days)
        return deleted_count
