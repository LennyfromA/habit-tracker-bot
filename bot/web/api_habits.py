"""REST API endpoints for Habits and Focus Timer."""

import logging
import time
from typing import Any, Dict, Optional

from aiohttp import web

from bot.database import habits_repo, tasks_repo, timers_repo, widgets_repo
from bot.services.widget_launcher import sync_widget_message
from bot.utils.formatting import calculate_timer_spent_seconds

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.post("/api/habits")
async def api_create_habit(request: web.Request) -> web.Response:
    """Create a new habit (checklist or duration)."""
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
            {"ok": False, "error": "Название должно содержать от 1 до 64 символов"},
            status=400,
        )

    habit_type = str(data.get("habit_type") or "duration").strip().lower()
    if habit_type not in ("duration", "checklist"):
        return web.json_response(
            {"ok": False, "error": "habit_type должен быть 'duration' или 'checklist'"},
            status=400,
        )

    try:
        target_minutes = int(data.get("target_minutes", 30))
    except (ValueError, TypeError):
        target_minutes = 30

    if habit_type == "duration":
        if target_minutes < 1 or target_minutes > 720:
            return web.json_response(
                {"ok": False, "error": "Цель в минутах должна быть от 1 до 720"},
                status=400,
            )
    else:
        target_minutes = 0

    try:
        target_repeats = int(data.get("target_repeats", 1))
    except (ValueError, TypeError):
        target_repeats = 1

    if habit_type == "checklist" and (target_repeats < 1 or target_repeats > 20):
        return web.json_response(
            {"ok": False, "error": "Количество повторений должно быть от 1 до 20"},
            status=400,
        )

    active_days = str(data.get("active_days") or "1,2,3,4,5,6,7").strip()
    reminder_times = data.get("reminder_times")
    if reminder_times is not None:
        reminder_times = str(reminder_times).strip() or None

    habit_id = await habits_repo.create_habit(
        user_id=user_id,
        name=name,
        target_minutes=target_minutes,
        active_days=active_days,
        habit_type=habit_type,
        target_repeats=target_repeats,
        reminder_times=reminder_times,
    )

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "habits")

    return web.json_response({
        "ok": True,
        "data": {"habit_id": habit_id},
        "synced": synced,
    }, status=201)


@routes.patch("/api/habits/{habit_id}")
async def api_update_habit(request: web.Request) -> web.Response:
    """Update existing habit properties with ownership check."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        habit_id = int(request.match_info["habit_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid habit_id"}, status=400)

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        return web.json_response({"ok": False, "error": "Привычка не найдена"}, status=404)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    name = data.get("name")
    if name is not None:
        name = str(name).strip()
        if not name or len(name) > 64:
            return web.json_response(
                {"ok": False, "error": "Название должно содержать от 1 до 64 символов"},
                status=400,
            )

    target_minutes = data.get("target_minutes")
    if target_minutes is not None:
        try:
            target_minutes = int(target_minutes)
            if target_minutes < 0 or target_minutes > 720:
                return web.json_response(
                    {"ok": False, "error": "Цель в минутах должна быть от 0 до 720"},
                    status=400,
                )
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректное значение target_minutes"}, status=400)

    target_repeats = data.get("target_repeats")
    if target_repeats is not None:
        try:
            target_repeats = int(target_repeats)
            if target_repeats < 1 or target_repeats > 20:
                return web.json_response(
                    {"ok": False, "error": "Количество повторений должно быть от 1 до 20"},
                    status=400,
                )
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректное значение target_repeats"}, status=400)

    active_days = data.get("active_days")
    if active_days is not None:
        active_days = str(active_days).strip()

    reminder_times = data.get("reminder_times")
    if reminder_times is not None:
        reminder_times = str(reminder_times).strip() or None

    habit_type = data.get("habit_type")
    if habit_type is not None:
        habit_type = str(habit_type).strip().lower()
        if habit_type not in ("duration", "checklist"):
            return web.json_response({"ok": False, "error": "Некорректный habit_type"}, status=400)

    updated = await habits_repo.update_habit(
        habit_id=habit_id,
        user_id=user_id,
        name=name,
        target_minutes=target_minutes,
        active_days=active_days,
        habit_type=habit_type,
        target_repeats=target_repeats,
        reminder_times=reminder_times,
    )

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "habits")

    return web.json_response({"ok": True, "data": updated, "synced": synced})


@routes.delete("/api/habits/{habit_id}")
async def api_delete_habit(request: web.Request) -> web.Response:
    """Delete a habit with ownership check."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        habit_id = int(request.match_info["habit_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid habit_id"}, status=400)

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        return web.json_response({"ok": False, "error": "Привычка не найдена"}, status=404)

    success = await habits_repo.delete_habit(habit_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "habits")

    return web.json_response({"ok": success, "synced": synced})


@routes.post("/api/habits/{habit_id}/check")
async def api_check_habit(request: web.Request) -> web.Response:
    """Mark habit execution (+1 repeat for checklist or full completion for duration)."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        habit_id = int(request.match_info["habit_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid habit_id"}, status=400)

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        return web.json_response({"ok": False, "error": "Привычка не найдена"}, status=404)

    habit_type = habit.get("habit_type", "duration")
    if habit_type == "checklist":
        cur_reps, tgt_reps, is_completed = await habits_repo.increment_habit_repeat(
            habit_id=habit_id,
            user_id=user_id,
            logged_date=local_date,
        )
        result_data = {
            "habit_type": "checklist",
            "current_repeats": cur_reps,
            "target_repeats": tgt_reps,
            "is_completed": is_completed,
        }
    else:
        target_minutes = habit.get("target_minutes", 30)
        await habits_repo.set_habit_completed_manual(
            habit_id=habit_id,
            user_id=user_id,
            logged_date=local_date,
            target_minutes=target_minutes,
        )
        result_data = {
            "habit_type": "duration",
            "spent_minutes": target_minutes,
            "target_minutes": target_minutes,
            "is_completed": True,
        }

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "habits")

    return web.json_response({"ok": True, "data": result_data, "synced": synced})


@routes.post("/api/habits/{habit_id}/freeze")
async def api_freeze_habit(request: web.Request) -> web.Response:
    """Freeze habit streak for today."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        habit_id = int(request.match_info["habit_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid habit_id"}, status=400)

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        return web.json_response({"ok": False, "error": "Привычка не найдена"}, status=404)

    success, msg = await habits_repo.freeze_habit_today(habit_id, user_id, logged_date=local_date)

    synced = False
    if bot and success:
        synced = await sync_widget_message(bot, user_id, wid, "habits")

    return web.json_response({
        "ok": success,
        "message": msg,
        "synced": synced,
    }, status=200 if success else 400)


# ---------------------------------------------------------------------------
# Focus Timer Endpoints
# ---------------------------------------------------------------------------

@routes.post("/api/timer/start")
async def api_timer_start(request: web.Request) -> web.Response:
    """Start or replace focus session timer (for either habit or task)."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
        habit_id = data.get("habit_id")
        task_id = data.get("task_id")
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request body"}, status=400)

    if not habit_id and not task_id:
        return web.json_response({"ok": False, "error": "habit_id or task_id is required"}, status=400)

    active = await timers_repo.get_timer(user_id)
    if active:
        return web.json_response(
            {"ok": False, "error": "Таймер уже запущен. Завершите или остановите текущий таймер."},
            status=409,
        )

    # Retrieve widget card message coordinates if wid is provided
    chat_id = user_id
    msg_id = 0
    if wid:
        w_rec = await widgets_repo.get_widget_message(wid, user_id)
        if w_rec:
            chat_id = w_rec["chat_id"]
            msg_id = w_rec["message_id"]

    if task_id:
        task_id = int(task_id)
        task = await tasks_repo.get_task_by_id(task_id, user_id)
        if not task:
            return web.json_response({"ok": False, "error": "Задача не найдена"}, status=404)
        target_minutes = int(data.get("duration_minutes") or task.get("estimated_minutes") or 25)
        total_target_seconds = target_minutes * 60

        await timers_repo.create_or_replace_timer(
            user_id=user_id,
            task_id=task_id,
            total_target_seconds=total_target_seconds,
            message_id=msg_id,
            chat_id=chat_id,
            started_at=int(time.time()),
        )
    else:
        habit_id = int(habit_id)
        habit = await habits_repo.get_habit_by_id(habit_id)
        if not habit or habit["user_id"] != user_id:
            return web.json_response({"ok": False, "error": "Привычка не найдена"}, status=404)
        target_minutes = habit.get("target_minutes", 30) or 30
        total_target_seconds = target_minutes * 60

        await timers_repo.create_or_replace_timer(
            user_id=user_id,
            habit_id=habit_id,
            total_target_seconds=total_target_seconds,
            message_id=msg_id,
            chat_id=chat_id,
            started_at=int(time.time()),
        )

    timer = await timers_repo.get_timer(user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "timer")

    return web.json_response({"ok": True, "data": timer, "synced": synced}, status=201)


@routes.post("/api/timer/pause")
async def api_timer_pause(request: web.Request) -> web.Response:
    """Pause running timer."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    timer = await timers_repo.pause_timer(user_id)
    if not timer:
        return web.json_response({"ok": False, "error": "Активный таймер не найден"}, status=404)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "timer")

    return web.json_response({"ok": True, "data": timer, "synced": synced})


@routes.post("/api/timer/resume")
async def api_timer_resume(request: web.Request) -> web.Response:
    """Resume paused timer."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    timer = await timers_repo.resume_timer(user_id)
    if not timer:
        return web.json_response({"ok": False, "error": "Активный таймер не найден"}, status=404)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "timer")

    return web.json_response({"ok": True, "data": timer, "synced": synced})


@routes.post("/api/timer/add-10")
async def api_timer_add_10(request: web.Request) -> web.Response:
    """Add 10 minutes (600s) to active timer."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    timer = await timers_repo.add_time_to_timer(user_id, additional_seconds=600)
    if not timer:
        return web.json_response({"ok": False, "error": "Активный таймер не найден"}, status=404)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "timer")

    return web.json_response({"ok": True, "data": timer, "synced": synced})


@routes.post("/api/timer/stop")
async def api_timer_stop(request: web.Request) -> web.Response:
    """Stop active timer atomically, record spent minutes, and return summary for reflection."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")
    now_ts = int(time.time())

    timer = await timers_repo.delete_timer(user_id)
    if not timer:
        return web.json_response({"ok": False, "error": "Активный таймер не найден"}, status=404)

    spent_seconds = calculate_timer_spent_seconds(timer, now_ts)
    actual_minutes = round(spent_seconds / 60)
    if spent_seconds >= 30 and actual_minutes == 0:
        actual_minutes = 1

    # Check if timer was for a task
    if timer.get("task_id") or timer.get("target_type") == "task":
        task_id = timer["task_id"]
        task_name = timer.get("target_name") or timer.get("habit_name", "")
        if actual_minutes > 0:
            await tasks_repo.add_task_spent_minutes(task_id, actual_minutes)
        summary = {
            "target_type": "task",
            "task_id": task_id,
            "task_name": task_name,
            "habit_name": task_name,
            "actual_minutes": actual_minutes,
            "target_minutes": timer.get("target_minutes", 25),
            "is_completed": False,
        }
        synced = False
        if bot:
            synced = await sync_widget_message(bot, user_id, wid, "timer")
        return web.json_response({"ok": True, "data": summary, "synced": synced})

    habit_id = timer["habit_id"]
    habit_name = timer.get("habit_name", "")
    target_minutes = timer.get("target_minutes", 30)

    spent_today = 0
    is_completed = False
    if actual_minutes > 0:
        spent_today, is_completed = await habits_repo.upsert_habit_log(
            habit_id=habit_id,
            user_id=user_id,
            logged_date=local_date,
            add_minutes=actual_minutes,
            target_minutes=target_minutes,
        )

    summary = {
        "actual_minutes": actual_minutes,
        "spent_today": spent_today,
        "target_minutes": target_minutes,
        "is_completed": is_completed,
        "habit_id": habit_id,
        "habit_name": habit_name,
        "logged_date": local_date,
    }

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "timer")

    return web.json_response({"ok": True, "data": summary, "synced": synced})


@routes.post("/api/timer/reflection")
async def api_timer_reflection(request: web.Request) -> web.Response:
    """Save reflection note for the habit session."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
        habit_id = int(data.get("habit_id"))
        logged_date = str(data.get("logged_date") or auth["local_date"]).strip()
        note = str(data.get("note") or "").strip()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid request body"}, status=400)

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        return web.json_response({"ok": False, "error": "Привычка не найдена"}, status=404)

    await habits_repo.update_habit_log_notes(habit_id, logged_date, note)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "timer")

    return web.json_response({"ok": True, "synced": synced})
