"""REST API endpoints for Task Tracker: lists, tasks, subtasks, scheduling, and chat sync."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aiohttp import web

from bot.database import tasks_repo
from bot.services.widget_launcher import sync_widget_message

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


# ---------------------------------------------------------------------------
# Task Lists Endpoints
# ---------------------------------------------------------------------------

@routes.get("/api/task-lists")
async def api_get_task_lists(request: web.Request) -> web.Response:
    """Retrieve all task lists for the authenticated user."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    lists = await tasks_repo.get_lists(user_id)
    return web.json_response({"ok": True, "data": lists})


@routes.post("/api/task-lists")
async def api_create_task_list(request: web.Request) -> web.Response:
    """Create a new custom task list."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    name = str(data.get("name") or "").strip()
    if not name or len(name) > 64:
        return web.json_response(
            {"ok": False, "error": "Название списка должно содержать от 1 до 64 символов"},
            status=400,
        )

    icon = str(data.get("icon") or "📁").strip() or "📁"

    try:
        list_id = await tasks_repo.create_list(user_id, name, icon)
    except Exception as e:
        return web.json_response({"ok": False, "error": "Список с таким именем уже существует"}, status=409)

    lists = await tasks_repo.get_lists(user_id)
    created_list = next((l for l in lists if l["id"] == list_id), None)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "data": created_list, "synced": synced})


@routes.patch("/api/task-lists/{list_id}")
async def api_update_task_list(request: web.Request) -> web.Response:
    """Update name or icon of an existing task list."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        list_id = int(request.match_info["list_id"])
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request"}, status=400)

    name = data.get("name")
    if name is not None:
        name = str(name).strip()
        if not name or len(name) > 64:
            return web.json_response({"ok": False, "error": "Некорректное имя списка"}, status=400)

    icon = data.get("icon")
    if icon is not None:
        icon = str(icon).strip() or "📁"

    updated = await tasks_repo.rename_list(list_id, user_id, new_name=name or "", new_icon=icon)
    if not updated:
        return web.json_response({"ok": False, "error": "Список не найден или защищен"}, status=404)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "synced": synced})


@routes.delete("/api/task-lists/{list_id}")
async def api_delete_task_list(request: web.Request) -> web.Response:
    """Delete a custom task list; its tasks are atomically moved to Inbox."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        list_id = int(request.match_info["list_id"])
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid list_id"}, status=400)

    deleted = await tasks_repo.delete_list(list_id, user_id)
    if not deleted:
        return web.json_response(
            {"ok": False, "error": "Нельзя удалить папку «Входящие» или список не найден"},
            status=400,
        )

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "synced": synced})


# ---------------------------------------------------------------------------
# Tasks Endpoints
# ---------------------------------------------------------------------------

@routes.get("/api/tasks")
async def api_get_tasks(request: web.Request) -> web.Response:
    """Retrieve tasks with optional view filters: 'today', 'inbox', 'all', or ?list_id=X."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]

    view = request.query.get("view", "today").strip().lower()
    list_id_param = request.query.get("list_id")

    if list_id_param:
        try:
            lid = int(list_id_param)
            tasks = await tasks_repo.get_tasks_by_list(user_id, lid)
        except ValueError:
            tasks = []
    elif view == "all":
        tasks = await tasks_repo.get_all_active_tasks(user_id)
    elif view == "inbox":
        inbox = await tasks_repo.ensure_inbox_list(user_id)
        tasks = await tasks_repo.get_tasks_by_list(user_id, inbox["id"])
    else:
        # Default: today view (includes overdue uncompleted + today due + today completed)
        tasks = await tasks_repo.get_today_tasks(user_id, local_date)

    lists = await tasks_repo.get_lists(user_id)
    today_tasks = await tasks_repo.get_today_tasks(user_id, local_date)

    counts = {
        "today_total": len(today_tasks),
        "today_completed": sum(1 for t in today_tasks if t.get("is_completed")),
        "overdue": sum(1 for t in today_tasks if t.get("is_overdue") and not t.get("is_completed")),
    }

    return web.json_response({
        "ok": True,
        "data": {
            "tasks": tasks,
            "lists": lists,
            "counts": counts,
        },
    })


@routes.post("/api/tasks")
async def api_create_task(request: web.Request) -> web.Response:
    """Create a new task with optional subtasks."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    title = str(data.get("title") or "").strip()
    if not title or len(title) > 255:
        return web.json_response(
            {"ok": False, "error": "Название задачи обязательно (до 255 символов)"},
            status=400,
        )

    list_id = data.get("list_id")
    if not list_id:
        inbox = await tasks_repo.ensure_inbox_list(user_id)
        list_id = inbox["id"]
    else:
        list_id = int(list_id)

    notes = data.get("notes")
    due_date = data.get("due_date") or None
    due_time = data.get("due_time") or None

    # Validate that due_time on today is not in the past
    if due_date and due_time:
        user = await users_repo.get_user(user_id)
        offset = user.get("utc_offset", 3) if user else 3
        local_now = datetime.now(timezone.utc) + timedelta(hours=offset)
        local_today_str = local_now.strftime("%Y-%m-%d")
        local_hm_str = local_now.strftime("%H:%M")
        if due_date < local_today_str or (due_date == local_today_str and due_time < local_hm_str):
            return web.json_response(
                {"ok": False, "error": "Нельзя установить время напоминания в прошлом"},
                status=400,
            )

    recurrence = str(data.get("recurrence") or "none").strip().lower()
    if recurrence not in ("none", "daily", "weekdays", "weekly"):
        recurrence = "none"

    estimated_minutes = data.get("estimated_minutes")
    if estimated_minutes is not None:
        try:
            estimated_minutes = max(1, min(720, int(estimated_minutes)))
        except (ValueError, TypeError):
            estimated_minutes = None

    task_id = await tasks_repo.create_task(
        user_id=user_id,
        list_id=list_id,
        title=title,
        notes=notes,
        due_date=due_date,
        due_time=due_time,
        recurrence=recurrence,
        estimated_minutes=estimated_minutes,
    )

    # Optional subtasks creation
    raw_subtasks = data.get("subtasks")
    if isinstance(raw_subtasks, list):
        for idx, sub_title in enumerate(raw_subtasks):
            clean_sub = str(sub_title).strip()
            if clean_sub:
                await tasks_repo.create_subtask(task_id, user_id, clean_sub, sort_order=idx)

    created_task = await tasks_repo.get_task_by_id(task_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "data": created_task, "synced": synced})


@routes.patch("/api/tasks/{task_id}")
async def api_update_task(request: web.Request) -> web.Response:
    """Update task details."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        task_id = int(request.match_info["task_id"])
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request"}, status=400)

    task = await tasks_repo.get_task_by_id(task_id, user_id)
    if not task:
        return web.json_response({"ok": False, "error": "Задача не найдена"}, status=404)

    fields_to_update = {}
    if "title" in data:
        t = str(data["title"]).strip()
        if not t or len(t) > 255:
            return web.json_response({"ok": False, "error": "Некорректное название"}, status=400)
        fields_to_update["title"] = t

    if "list_id" in data:
        fields_to_update["list_id"] = int(data["list_id"])
    if "notes" in data:
        fields_to_update["notes"] = data["notes"]
    if "due_date" in data:
        fields_to_update["due_date"] = data["due_date"] or None
    if "due_time" in data:
        fields_to_update["due_time"] = data["due_time"] or None
    if "recurrence" in data:
        r = str(data["recurrence"]).strip().lower()
        if r in ("none", "daily", "weekdays", "weekly"):
            fields_to_update["recurrence"] = r
    if "estimated_minutes" in data:
        val = data["estimated_minutes"]
        fields_to_update["estimated_minutes"] = int(val) if val is not None else None

    if fields_to_update:
        await tasks_repo.update_task(task_id, user_id, **fields_to_update)

    updated_task = await tasks_repo.get_task_by_id(task_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "data": updated_task, "synced": synced})


@routes.delete("/api/tasks/{task_id}")
async def api_delete_task(request: web.Request) -> web.Response:
    """Delete a task and its subtasks."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        task_id = int(request.match_info["task_id"])
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid task_id"}, status=400)

    deleted = await tasks_repo.delete_task(task_id, user_id)
    if not deleted:
        return web.json_response({"ok": False, "error": "Задача не найдена"}, status=404)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "synced": synced})


@routes.post("/api/tasks/{task_id}/toggle")
async def api_toggle_task(request: web.Request) -> web.Response:
    """Toggle task completion with atomic recurrence rollover."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        task_id = int(request.match_info["task_id"])
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid task_id"}, status=400)

    task = await tasks_repo.get_task_by_id(task_id, user_id)
    if not task:
        return web.json_response({"ok": False, "error": "Задача не найдена"}, status=404)

    new_status = await tasks_repo.toggle_task_complete(task_id, user_id, local_date)
    updated_task = await tasks_repo.get_task_by_id(task_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({
        "ok": True,
        "is_completed": bool(new_status),
        "data": updated_task,
        "synced": synced,
    })


@routes.post("/api/tasks/{task_id}/reschedule")
async def api_reschedule_task(request: web.Request) -> web.Response:
    """Reschedule task to a new due date and optional time."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        task_id = int(request.match_info["task_id"])
        data = await request.json()
        new_due_date = str(data["due_date"]).strip()
        new_due_time = data.get("due_time")
    except Exception:
        return web.json_response({"ok": False, "error": "due_date is required"}, status=400)

    updated = await tasks_repo.reschedule_task(task_id, user_id, new_due_date, new_due_time)
    if not updated:
        return web.json_response({"ok": False, "error": "Задача не найдена"}, status=404)

    updated_task = await tasks_repo.get_task_by_id(task_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "data": updated_task, "synced": synced})


@routes.post("/api/tasks/defer-today")
async def api_defer_today_tasks(request: web.Request) -> web.Response:
    """Bulk reschedule all uncompleted tasks due today to tomorrow."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    count = await tasks_repo.defer_today_tasks_to_tomorrow(user_id, local_date)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "deferred_count": count, "synced": synced})


# ---------------------------------------------------------------------------
# Subtasks Endpoints (Strictly 1 Level Deep)
# ---------------------------------------------------------------------------

@routes.post("/api/tasks/{task_id}/subtasks")
async def api_create_subtask(request: web.Request) -> web.Response:
    """Create a new subtask for a parent task."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        task_id = int(request.match_info["task_id"])
        data = await request.json()
        title = str(data.get("title") or "").strip()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request"}, status=400)

    if not title or len(title) > 255:
        return web.json_response({"ok": False, "error": "Название подзадачи обязательно"}, status=400)

    task = await tasks_repo.get_task_by_id(task_id, user_id)
    if not task:
        return web.json_response({"ok": False, "error": "Родительская задача не найдена"}, status=404)

    sub_id = await tasks_repo.create_subtask(task_id, user_id, title)
    updated_task = await tasks_repo.get_task_by_id(task_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({
        "ok": True,
        "data": {"id": sub_id, "task_id": task_id, "title": title, "is_completed": 0},
        "task": updated_task,
        "synced": synced,
    })


@routes.patch("/api/subtasks/{subtask_id}")
async def api_update_subtask(request: web.Request) -> web.Response:
    """Update title or toggle completion of a subtask."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        subtask_id = int(request.match_info["subtask_id"])
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request"}, status=400)

    if "is_completed" in data:
        new_val = bool(data["is_completed"])
        updated = await tasks_repo.toggle_subtask(subtask_id, user_id, is_completed=new_val)
        if not updated:
            return web.json_response({"ok": False, "error": "Подзадача не найдена"}, status=404)

    if "title" in data:
        t = str(data["title"]).strip()
        if not t or len(t) > 255:
            return web.json_response({"ok": False, "error": "Некорректное название"}, status=400)
        await tasks_repo.update_subtask(subtask_id, user_id, title=t)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "synced": synced})


@routes.delete("/api/subtasks/{subtask_id}")
async def api_delete_subtask(request: web.Request) -> web.Response:
    """Delete a subtask."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        subtask_id = int(request.match_info["subtask_id"])
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid subtask_id"}, status=400)

    deleted = await tasks_repo.delete_subtask(subtask_id, user_id)
    if not deleted:
        return web.json_response({"ok": False, "error": "Подзадача не найдена"}, status=404)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "tasks")

    return web.json_response({"ok": True, "synced": synced})
