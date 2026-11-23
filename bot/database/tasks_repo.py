"""Repository for managing Todoist-style Task Lists, Tasks, Subtasks, and Reminders."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bot.database.db import get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task Lists
# ---------------------------------------------------------------------------

async def ensure_inbox_list(user_id: int) -> Dict[str, Any]:
    """Idempotently ensure the user has a default Inbox list and return it."""
    async with get_db() as db:
        # Check existing inbox
        query_find = "SELECT * FROM task_lists WHERE user_id = ? AND is_inbox = 1 LIMIT 1;"
        async with db.execute(query_find, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)

        # Check by name 'Входящие'
        query_by_name = "SELECT * FROM task_lists WHERE user_id = ? AND LOWER(name) = 'входящие' LIMIT 1;"
        async with db.execute(query_by_name, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE task_lists SET is_inbox = 1, sort_order = 0 WHERE id = ?;", (row["id"],))
                await db.commit()
                return {**dict(row), "is_inbox": 1, "sort_order": 0}

        # Create Inbox list
        insert_q = """
        INSERT INTO task_lists (user_id, name, icon, is_inbox, sort_order)
        VALUES (?, 'Входящие', '📥', 1, 0);
        """
        cursor = await db.execute(insert_q, (user_id,))
        await db.commit()
        inbox_id = cursor.lastrowid

        async with db.execute("SELECT * FROM task_lists WHERE id = ?;", (inbox_id,)) as c:
            created = await c.fetchone()
            return dict(created)


async def get_lists(user_id: int) -> List[Dict[str, Any]]:
    """Return all task lists with active (uncompleted) task counts."""
    await ensure_inbox_list(user_id)
    async with get_db() as db:
        query = """
        SELECT
            l.id,
            l.user_id,
            l.name,
            l.icon,
            l.is_inbox,
            l.sort_order,
            l.created_at,
            COUNT(CASE WHEN t.is_completed = 0 THEN 1 END) AS task_count
        FROM task_lists l
        LEFT JOIN tasks t ON t.list_id = l.id AND t.user_id = l.user_id
        WHERE l.user_id = ?
        GROUP BY l.id
        ORDER BY l.is_inbox DESC, l.sort_order ASC, l.id ASC;
        """
        async with db.execute(query, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_list_by_name(user_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Case-insensitive lookup of a list by name for a user."""
    cleaned = name.strip()
    if not cleaned:
        return None
    async with get_db() as db:
        query = "SELECT * FROM task_lists WHERE user_id = ? AND LOWER(name) = LOWER(?) LIMIT 1;"
        async with db.execute(query, (user_id, cleaned)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_list(user_id: int, name: str, icon: str = "📁") -> Dict[str, Any]:
    """Create a new custom list for the user."""
    name_clean = name.strip()
    if not name_clean or len(name_clean) > 40:
        raise ValueError("List name must be between 1 and 40 characters")

    existing = await get_list_by_name(user_id, name_clean)
    if existing:
        return existing

    async with get_db() as db:
        async with db.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM task_lists WHERE user_id = ?;",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            next_order = row["next_order"] if row else 1

        insert_q = """
        INSERT INTO task_lists (user_id, name, icon, is_inbox, sort_order)
        VALUES (?, ?, ?, 0, ?);
        """
        cursor = await db.execute(insert_q, (user_id, name_clean, icon.strip() or "📁", next_order))
        await db.commit()
        return cursor.lastrowid


async def rename_list(
    list_id: int,
    user_id: int,
    name: Optional[str] = None,
    icon: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Rename or update icon of a custom list (cannot rename Inbox)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM task_lists WHERE id = ? AND user_id = ?;",
            (list_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row["is_inbox"]:
                return None  # Cannot rename system Inbox or non-existent list

        updates = []
        params = []
        if name is not None:
            clean_name = name.strip()
            if not clean_name or len(clean_name) > 40:
                raise ValueError("List name must be between 1 and 40 characters")
            updates.append("name = ?")
            params.append(clean_name)
        if icon is not None:
            updates.append("icon = ?")
            params.append(icon.strip() or "📁")

        if not updates:
            return dict(row)

        params.extend([list_id, user_id])
        query = f"UPDATE task_lists SET {', '.join(updates)} WHERE id = ? AND user_id = ?;"
        await db.execute(query, tuple(params))
        await db.commit()

        async with db.execute("SELECT * FROM task_lists WHERE id = ?;", (list_id,)) as c:
            return dict(await c.fetchone())


async def delete_list(list_id: int, user_id: int) -> bool:
    """Atomically reassign all tasks in list to Inbox before deleting the custom list."""
    inbox = await ensure_inbox_list(user_id)
    inbox_id = inbox["id"]
    if list_id == inbox_id:
        return False  # Cannot delete Inbox

    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM task_lists WHERE id = ? AND user_id = ?;",
            (list_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row["is_inbox"]:
                return False

        await db.execute("BEGIN IMMEDIATE;")
        # Reassign tasks to user's Inbox
        await db.execute(
            "UPDATE tasks SET list_id = ? WHERE list_id = ? AND user_id = ?;",
            (inbox_id, list_id, user_id),
        )
        # Delete custom list
        await db.execute("DELETE FROM task_lists WHERE id = ? AND user_id = ?;", (list_id, user_id))
        await db.commit()
        return True


# ---------------------------------------------------------------------------
# Internal Subtasks Attacher (Zero N+1 query overhead)
# ---------------------------------------------------------------------------

async def _attach_subtasks(db, tasks: List[Dict[str, Any]], user_id: int) -> List[Dict[str, Any]]:
    """Efficiently fetch and attach nested subtasks to a list of task records in a single query."""
    if not tasks:
        return []

    task_ids = [t["id"] for t in tasks]
    placeholders = ",".join("?" for _ in task_ids)
    query = f"""
    SELECT id, task_id, user_id, title, is_completed, sort_order, created_at
    FROM task_subtasks
    WHERE task_id IN ({placeholders}) AND user_id = ?
    ORDER BY sort_order ASC, id ASC;
    """
    params = list(task_ids) + [user_id]
    subtasks_by_task: Dict[int, List[Dict[str, Any]]] = {tid: [] for tid in task_ids}

    async with db.execute(query, params) as cursor:
        for r in await cursor.fetchall():
            subtasks_by_task[r["task_id"]].append(dict(r))

    for t in tasks:
        st_list = subtasks_by_task.get(t["id"], [])
        t["subtasks"] = st_list
        t["subtasks_total"] = len(st_list)
        t["subtasks_done"] = sum(1 for st in st_list if st["is_completed"])

    return tasks


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

async def create_task(
    user_id: int,
    title: str,
    list_id: Optional[int] = None,
    due_date: Optional[str] = None,
    due_time: Optional[str] = None,
    recurrence: str = "none",
    estimated_minutes: Optional[int] = None,
    notes: Optional[str] = None,
    subtasks: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new task with optional initial subtasks."""
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Task title cannot be empty")

    if recurrence not in ("none", "daily", "weekdays", "weekly"):
        recurrence = "none"

    if estimated_minutes is not None:
        estimated_minutes = max(1, min(720, int(estimated_minutes)))

    if isinstance(list_id, dict):
        list_id = list_id.get("id")

    inbox = await ensure_inbox_list(user_id)
    target_list_id = list_id if list_id is not None else inbox["id"]

    async with get_db() as db:
        # Verify list belongs to user
        async with db.execute(
            "SELECT id FROM task_lists WHERE id = ? AND user_id = ?;",
            (target_list_id, user_id),
        ) as cursor:
            if not await cursor.fetchone():
                target_list_id = inbox["id"]

        insert_q = """
        INSERT INTO tasks (
            user_id, list_id, title, notes, due_date, due_time,
            recurrence, estimated_minutes, spent_minutes, is_completed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0);
        """
        cursor = await db.execute(
            insert_q,
            (user_id, target_list_id, clean_title, notes, due_date, due_time, recurrence, estimated_minutes),
        )
        task_id = cursor.lastrowid

        # Insert initial subtasks
        if subtasks:
            for idx, st_title in enumerate(subtasks):
                st_clean = st_title.strip()
                if st_clean:
                    await db.execute(
                        """
                        INSERT INTO task_subtasks (task_id, user_id, title, is_completed, sort_order)
                        VALUES (?, ?, ?, 0, ?);
                        """,
                        (task_id, user_id, st_clean, idx),
                    )

        await db.commit()

    return task_id


async def get_task_by_id(task_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve task by id with list info and nested subtasks."""
    if isinstance(task_id, dict):
        task_id = task_id.get("id")
    async with get_db() as db:
        query = """
        SELECT
            t.*,
            l.name AS list_name,
            l.icon AS list_icon
        FROM tasks t
        LEFT JOIN task_lists l ON l.id = t.list_id
        WHERE t.id = ? AND t.user_id = ?;
        """
        async with db.execute(query, (task_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            task_dict = dict(row)

        attached = await _attach_subtasks(db, [task_dict], user_id)
        return attached[0] if attached else None


async def get_today_tasks(user_id: int, local_date: str) -> List[Dict[str, Any]]:
    """
    Smart view 'Today' (Сегодня):
    Aggregates uncompleted tasks with due_date <= local_date (overdue + today)
    PLUS tasks completed today (completed_at == local_date).
    Annotates each task with is_overdue: bool.
    """
    await ensure_inbox_list(user_id)
    async with get_db() as db:
        query = """
        SELECT
            t.*,
            l.name AS list_name,
            l.icon AS list_icon,
            CASE
                WHEN t.is_completed = 0 AND t.due_date IS NOT NULL AND t.due_date < ? THEN 1
                ELSE 0
            END AS is_overdue
        FROM tasks t
        LEFT JOIN task_lists l ON l.id = t.list_id
        WHERE t.user_id = ?
          AND (
            (t.is_completed = 0 AND t.due_date IS NOT NULL AND t.due_date <= ?)
            OR
            (t.is_completed = 1 AND t.completed_at = ?)
          )
        ORDER BY
            t.is_completed ASC,
            is_overdue DESC,
            (t.due_time IS NULL) ASC,
            t.due_time ASC,
            t.id ASC;
        """
        async with db.execute(query, (local_date, user_id, local_date, local_date)) as cursor:
            rows = await cursor.fetchall()
            tasks = [dict(r) for r in rows]

        for t in tasks:
            t["is_overdue"] = bool(t["is_overdue"])

        return await _attach_subtasks(db, tasks, user_id)


async def get_tasks_by_list(
    user_id: int,
    list_id: int,
    include_completed: bool = True,
) -> List[Dict[str, Any]]:
    """Return all tasks in a specific list."""
    async with get_db() as db:
        completed_filter = "" if include_completed else "AND t.is_completed = 0"
        query = f"""
        SELECT
            t.*,
            l.name AS list_name,
            l.icon AS list_icon,
            0 AS is_overdue
        FROM tasks t
        LEFT JOIN task_lists l ON l.id = t.list_id
        WHERE t.user_id = ? AND t.list_id = ? {completed_filter}
        ORDER BY
            t.is_completed ASC,
            (t.due_date IS NULL) ASC,
            t.due_date ASC,
            (t.due_time IS NULL) ASC,
            t.due_time ASC,
            t.id ASC;
        """
        async with db.execute(query, (user_id, list_id)) as cursor:
            tasks = [dict(r) for r in await cursor.fetchall()]

        return await _attach_subtasks(db, tasks, user_id)


async def get_all_active_tasks(user_id: int) -> List[Dict[str, Any]]:
    """Return all uncompleted tasks for a user."""
    async with get_db() as db:
        query = """
        SELECT
            t.*,
            l.name AS list_name,
            l.icon AS list_icon,
            0 AS is_overdue
        FROM tasks t
        LEFT JOIN task_lists l ON l.id = t.list_id
        WHERE t.user_id = ? AND t.is_completed = 0
        ORDER BY
            (t.due_date IS NULL) ASC,
            t.due_date ASC,
            (t.due_time IS NULL) ASC,
            t.due_time ASC,
            t.id ASC;
        """
        async with db.execute(query, (user_id,)) as cursor:
            tasks = [dict(r) for r in await cursor.fetchall()]

        return await _attach_subtasks(db, tasks, user_id)


async def update_task(task_id: int, user_id: int, **fields) -> Optional[int]:
    """Partially update a task with ownership verification."""
    if isinstance(task_id, dict):
        task_id = task_id.get("id")
    allowed = {
        "title",
        "notes",
        "due_date",
        "due_time",
        "recurrence",
        "estimated_minutes",
        "list_id",
    }
    updates = []
    params = []

    if "recurrence" in fields and fields["recurrence"] not in ("none", "daily", "weekdays", "weekly"):
        fields["recurrence"] = "none"

    if "estimated_minutes" in fields and fields["estimated_minutes"] is not None:
        fields["estimated_minutes"] = max(1, min(720, int(fields["estimated_minutes"])))

    async with get_db() as db:
        # Check task existence & ownership
        async with db.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?;", (task_id, user_id)) as cursor:
            existing = await cursor.fetchone()
            if not existing:
                return None

        # If list_id is changing, verify ownership of destination list
        if "list_id" in fields and fields["list_id"] is not None:
            async with db.execute(
                "SELECT id FROM task_lists WHERE id = ? AND user_id = ?;",
                (fields["list_id"], user_id),
            ) as cursor:
                if not await cursor.fetchone():
                    return None

        for k, v in fields.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                params.append(v)

        if not updates:
            return await get_task_by_id(task_id, user_id)

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([task_id, user_id])
        query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ? AND user_id = ?;"
        await db.execute(query, tuple(params))
        await db.commit()

    return task_id


async def delete_task(task_id: int, user_id: int) -> bool:
    """Delete a task and its associated subtasks and reminders."""
    if isinstance(task_id, dict):
        task_id = task_id.get("id")
    async with get_db() as db:
        cursor = await db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?;", (task_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


def calculate_next_due_date(base_date_str: str, recurrence: str) -> str:
    """Calculate the next due date based on recurrence rule."""
    try:
        base_d = datetime.strptime(base_date_str, "%Y-%m-%d").date()
    except Exception:
        base_d = datetime.now().date()

    if recurrence == "daily":
        next_d = base_d + timedelta(days=1)
    elif recurrence == "weekdays":
        # Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6
        weekday = base_d.weekday()
        if weekday == 4:  # Friday -> Monday
            next_d = base_d + timedelta(days=3)
        elif weekday == 5:  # Saturday -> Monday
            next_d = base_d + timedelta(days=2)
        elif weekday == 6:  # Sunday -> Monday
            next_d = base_d + timedelta(days=1)
        else:  # Monday to Thursday -> Next Day
            next_d = base_d + timedelta(days=1)
    elif recurrence == "weekly":
        next_d = base_d + timedelta(days=7)
    else:
        next_d = base_d + timedelta(days=1)

    return next_d.strftime("%Y-%m-%d")


async def toggle_task_complete(
    task_id: int,
    user_id: int,
    local_date: str,
) -> Optional[Dict[str, Any]]:
    """
    Toggle task completion.
    For non-recurring: flips is_completed 0 <-> 1 and sets completed_at.
    For recurring tasks when completing:
      1. Creates a completed snapshot row in tasks (recurrence='none', is_completed=1, completed_at=local_date);
      2. Advances the active recurring task's due_date to the next cycle, resets spent_minutes=0, and unchecks subtasks;
      3. Returns the active task with rolled_over=True and next_due_date.
    """
    if isinstance(task_id, dict):
        task_id = task_id.get("id")

    async with get_db() as db:
        async with db.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?;", (task_id, user_id)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            task = dict(row)

        recurrence = task.get("recurrence", "none")
        is_completed = bool(task.get("is_completed"))

        if recurrence == "none" or is_completed:
            # Simple toggle
            new_completed = 0 if is_completed else 1
            completed_at = local_date if new_completed else None
            update_q = """
            UPDATE tasks
            SET is_completed = ?, completed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?;
            """
            await db.execute(update_q, (new_completed, completed_at, task_id, user_id))
            await db.commit()
            result = await get_task_by_id(task_id, user_id)
            if result:
                result["rolled_over"] = False
            return result

        # Recurring task being marked as complete -> Rollover!
        await db.execute("BEGIN IMMEDIATE;")

        # 1. Insert completed occurrence snapshot for stats and markdown export
        snapshot_q = """
        INSERT INTO tasks (
            user_id, list_id, title, notes, due_date, due_time,
            recurrence, estimated_minutes, spent_minutes, is_completed, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'none', ?, ?, 1, ?);
        """
        await db.execute(
            snapshot_q,
            (
                user_id,
                task["list_id"],
                task["title"],
                task["notes"],
                task["due_date"] or local_date,
                task["due_time"],
                task["estimated_minutes"],
                task["spent_minutes"],
                local_date,
            ),
        )

        # 2. Advance the active recurring task
        calc_base = local_date
        if task["due_date"] and task["due_date"] > local_date:
            calc_base = task["due_date"]

        next_due = calculate_next_due_date(calc_base, recurrence)

        advance_q = """
        UPDATE tasks
        SET due_date = ?, spent_minutes = 0, is_completed = 0, completed_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?;
        """
        await db.execute(advance_q, (next_due, task_id, user_id))

        # 3. Reset subtasks of active recurring task to unchecked
        await db.execute("UPDATE task_subtasks SET is_completed = 0 WHERE task_id = ? AND user_id = ?;", (task_id, user_id))
        await db.commit()

        result = await get_task_by_id(task_id, user_id)
        if result:
            result["rolled_over"] = True
            result["next_due_date"] = next_due
        return result


async def reschedule_task(
    task_id: int,
    user_id: int,
    due_date: Optional[str],
    due_time: Optional[str] = ...,
) -> Optional[Dict[str, Any]]:
    """Reschedule task to a new due_date and optionally due_time."""
    fields = {"due_date": due_date}
    if due_time is not ...:
        fields["due_time"] = due_time
    return await update_task(task_id, user_id, **fields)


async def defer_today_tasks_to_tomorrow(user_id: int, local_date: str) -> int:
    """Move all uncompleted tasks due on or before local_date to local_date + 1 day."""
    try:
        tomorrow_str = (datetime.strptime(local_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        tomorrow_str = local_date

    async with get_db() as db:
        query = """
        UPDATE tasks
        SET due_date = ?, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
          AND is_completed = 0
          AND due_date IS NOT NULL
          AND due_date <= ?;
        """
        cursor = await db.execute(query, (tomorrow_str, user_id, local_date))
        await db.commit()
        return cursor.rowcount


async def add_task_spent_minutes(
    task_id: int,
    add_minutes: int,
    user_id: Optional[int] = None,
) -> bool:
    """Atomically increment spent_minutes for a task."""
    if isinstance(task_id, dict):
        task_id = task_id.get("id")

    if add_minutes <= 0:
        return False

    async with get_db() as db:
        if user_id is not None:
            query = """
            UPDATE tasks
            SET spent_minutes = spent_minutes + ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?;
            """
            cursor = await db.execute(query, (add_minutes, task_id, user_id))
        else:
            query = """
            UPDATE tasks
            SET spent_minutes = spent_minutes + ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """
            cursor = await db.execute(query, (add_minutes, task_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_completed_tasks_in_range(
    user_id: int,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Retrieve all completed tasks for a user between start_date and end_date (inclusive)."""
    async with get_db() as db:
        query = """
        SELECT
            t.*,
            l.name AS list_name,
            l.icon AS list_icon
        FROM tasks t
        LEFT JOIN task_lists l ON l.id = t.list_id
        WHERE t.user_id = ?
          AND t.is_completed = 1
          AND t.completed_at IS NOT NULL
          AND t.completed_at BETWEEN ? AND ?
        ORDER BY t.completed_at DESC, t.id DESC;
        """
        async with db.execute(query, (user_id, start_date, end_date)) as cursor:
            tasks = [dict(r) for r in await cursor.fetchall()]

        return await _attach_subtasks(db, tasks, user_id)


# ---------------------------------------------------------------------------
# Subtasks
# ---------------------------------------------------------------------------

async def create_subtask(
    task_id: int,
    user_id: int,
    title: str,
    sort_order: Optional[int] = None,
) -> Optional[int]:
    """Add a subtask to a task (verifying user ownership of the parent task) and return subtask_id."""
    if isinstance(task_id, dict):
        task_id = task_id.get("id")

    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Subtask title cannot be empty")

    async with get_db() as db:
        # Verify parent task ownership
        async with db.execute("SELECT id FROM tasks WHERE id = ? AND user_id = ?;", (task_id, user_id)) as c:
            if not await c.fetchone():
                return None

        if sort_order is None:
            async with db.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM task_subtasks WHERE task_id = ?;",
                (task_id,),
            ) as c:
                sort_order = (await c.fetchone())["next_order"]

        insert_q = """
        INSERT INTO task_subtasks (task_id, user_id, title, is_completed, sort_order)
        VALUES (?, ?, ?, 0, ?);
        """
        cursor = await db.execute(insert_q, (task_id, user_id, clean_title, sort_order))
        await db.commit()
        return cursor.lastrowid


async def toggle_subtask(
    subtask_id: int,
    user_id: int,
    is_completed: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Toggle or set subtask completion state."""
    if isinstance(subtask_id, dict):
        subtask_id = subtask_id.get("id")

    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM task_subtasks WHERE id = ? AND user_id = ?;",
            (subtask_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            st = dict(row)

        if is_completed is not None:
            new_val = 1 if is_completed else 0
        else:
            new_val = 0 if st["is_completed"] else 1

        await db.execute(
            "UPDATE task_subtasks SET is_completed = ? WHERE id = ? AND user_id = ?;",
            (new_val, subtask_id, user_id),
        )
        await db.commit()

        async with db.execute("SELECT * FROM task_subtasks WHERE id = ?;", (subtask_id,)) as c:
            return dict(await c.fetchone())


async def update_subtask(
    subtask_id: int,
    user_id: int,
    title: Optional[str] = None,
    is_completed: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Update title or completion of a subtask."""
    if isinstance(subtask_id, dict):
        subtask_id = subtask_id.get("id")

    async with get_db() as db:
        updates = []
        params = []
        if title is not None:
            clean = title.strip()
            if not clean:
                raise ValueError("Subtask title cannot be empty")
            updates.append("title = ?")
            params.append(clean)
        if is_completed is not None:
            updates.append("is_completed = ?")
            params.append(1 if is_completed else 0)

        if not updates:
            async with db.execute("SELECT * FROM task_subtasks WHERE id = ? AND user_id = ?;", (subtask_id, user_id)) as c:
                row = await c.fetchone()
                return dict(row) if row else None

        params.extend([subtask_id, user_id])
        query = f"UPDATE task_subtasks SET {', '.join(updates)} WHERE id = ? AND user_id = ?;"
        cursor = await db.execute(query, tuple(params))
        await db.commit()
        if cursor.rowcount == 0:
            return None

        async with db.execute("SELECT * FROM task_subtasks WHERE id = ?;", (subtask_id,)) as c:
            return dict(await c.fetchone())


async def delete_subtask(subtask_id: int, user_id: int) -> bool:
    """Delete a subtask."""
    if isinstance(subtask_id, dict):
        subtask_id = subtask_id.get("id")

    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM task_subtasks WHERE id = ? AND user_id = ?;",
            (subtask_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Task Reminders
# ---------------------------------------------------------------------------

async def get_due_task_reminders_candidates(
    user_id: int, local_date: str, local_time: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get active uncompleted tasks scheduled for local_date that have due_time configured.
    If local_time is provided, only returns tasks due at or before local_time.
    """
    async with get_db() as db:
        if local_time:
            query = """
            SELECT
                t.*,
                l.name AS list_name,
                l.icon AS list_icon
            FROM tasks t
            LEFT JOIN task_lists l ON l.id = t.list_id
            WHERE t.user_id = ?
              AND t.is_completed = 0
              AND t.due_date = ?
              AND t.due_time IS NOT NULL
              AND t.due_time <= ?;
            """
            params = (user_id, local_date, local_time)
        else:
            query = """
            SELECT
                t.*,
                l.name AS list_name,
                l.icon AS list_icon
            FROM tasks t
            LEFT JOIN task_lists l ON l.id = t.list_id
            WHERE t.user_id = ?
              AND t.is_completed = 0
              AND t.due_date = ?
              AND t.due_time IS NOT NULL;
            """
            params = (user_id, local_date)

        async with db.execute(query, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def was_task_reminder_sent(task_id: int, reminded_date: str, reminder_time: str) -> bool:
    """Check if reminder was already recorded for task at date and time."""
    async with get_db() as db:
        query = """
        SELECT id FROM task_reminders_log
        WHERE task_id = ? AND reminded_date = ? AND reminder_time = ?
        LIMIT 1;
        """
        async with db.execute(query, (task_id, reminded_date, reminder_time)) as cursor:
            return bool(await cursor.fetchone())


async def record_task_reminder_sent(
    task_id: int,
    user_id: int,
    reminded_date: str,
    reminder_time: str,
) -> None:
    """Record that a task reminder was successfully sent."""
    async with get_db() as db:
        query = """
        INSERT OR IGNORE INTO task_reminders_log (task_id, user_id, reminded_date, reminder_time)
        VALUES (?, ?, ?, ?);
        """
        await db.execute(query, (task_id, user_id, reminded_date, reminder_time))
        await db.commit()


async def cleanup_old_task_reminders(cutoff_date: str) -> int:
    """Delete reminder logs older than cutoff_date."""
    async with get_db() as db:
        cursor = await db.execute("DELETE FROM task_reminders_log WHERE reminded_date < ?;", (cutoff_date,))
        await db.commit()
        return cursor.rowcount
