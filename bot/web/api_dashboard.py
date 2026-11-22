"""REST API endpoints for dashboard bootstrap, settings management, and 7-day statistics."""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from aiohttp import web

from bot.database import habits_repo, nutrition_repo, tasks_repo, timers_repo, users_repo, water_repo, weight_repo
from bot.handlers.timer import calculate_timer_spent_seconds
from bot.services.widget_launcher import sync_widget_message

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


@routes.get("/api/bootstrap")
async def api_bootstrap(request: web.Request) -> web.Response:
    """Return unified, fast snapshot of all user state for instant widget rendering."""
    auth = request["auth"]
    user_id = auth["user_id"]
    local_date = auth["local_date"]
    local_weekday = auth["local_weekday"]
    utc_offset = auth["utc_offset"]
    server_ts = auth["server_ts"]

    # 1. Habits
    habits_today = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
    habits_all = await habits_repo.get_all_habits_with_today_status(user_id, local_date)

    # 2. Timer
    timer = await timers_repo.get_timer(user_id)
    timer_payload = None
    if timer:
        spent_sec = calculate_timer_spent_seconds(timer, server_ts)
        target_sec = timer["total_target_seconds"]
        timer_payload = {
            **timer,
            "spent_seconds": spent_sec,
            "spent_minutes": round(spent_sec / 60),
            "target_minutes": round(target_sec / 60),
            "remaining_seconds": max(0, target_sec - spent_sec),
        }

    # 3. Nutrition & Food
    nutr_goals = await nutrition_repo.get_nutrition_goals(user_id)
    nutr_summary = await nutrition_repo.get_daily_summary(user_id, local_date)
    raw_meals = await nutrition_repo.get_meals_for_date(user_id, local_date)

    meals = []
    for m in raw_meals:
        created_str = str(m.get("created_at") or "")
        time_hm = ""
        if len(created_str) >= 16:
            try:
                utc_dt = datetime.strptime(created_str[:19], "%Y-%m-%d %H:%M:%S")
                local_dt = utc_dt + timedelta(hours=utc_offset)
                time_hm = local_dt.strftime("%H:%M")
            except Exception:
                time_hm = created_str[11:16]
        meals.append({**m, "time_hm": time_hm})

    frequent_meals = await nutrition_repo.get_frequent_meals(user_id, limit=6)

    # 4. Water
    water_cur = await water_repo.get_daily_water(user_id, local_date)
    water_tgt = await water_repo.get_water_target(user_id)

    # 5. Weight
    weight_today = await weight_repo.get_weight_for_date(user_id, local_date)
    weight_stats = await weight_repo.get_weight_stats(user_id, local_date)

    # 6. Weekly 7-day stats
    end_dt = datetime.strptime(local_date, "%Y-%m-%d")
    dates_7 = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    start_date_str = dates_7[0]

    weekly_stats = await habits_repo.get_weekly_stats(user_id, start_date_str, local_date)

    # 7. Tasks & Lists
    task_lists = await tasks_repo.get_lists(user_id)
    tasks_today = await tasks_repo.get_today_tasks(user_id, local_date)
    all_active_tasks = await tasks_repo.get_all_active_tasks(user_id)
    completed_tasks_7d = await tasks_repo.get_completed_tasks_in_range(user_id, start_date_str, local_date)
    tasks_counts = {
        "today_total": len(tasks_today),
        "today_completed": sum(1 for t in tasks_today if t.get("is_completed")),
        "overdue": sum(1 for t in tasks_today if t.get("is_overdue") and not t.get("is_completed")),
        "all_active": len(all_active_tasks),
        "completed_7d": len(completed_tasks_7d),
    }

    # 8. Refresh user
    user = await users_repo.get_user(user_id)

    data = {
        "user": user,
        "local_date": local_date,
        "local_weekday": local_weekday,
        "utc_offset": utc_offset,
        "server_ts": server_ts,
        "habits_today": habits_today,
        "habits_all": habits_all,
        "task_lists": task_lists,
        "tasks_today": tasks_today,
        "tasks_counts": tasks_counts,
        "completed_tasks_7d_count": len(completed_tasks_7d),
        "active_timer": timer_payload,
        "nutrition": {
            "goals": nutr_goals,
            "summary": nutr_summary,
            "meals": meals,
            "frequent_meals": frequent_meals,
        },
        "water": {
            "current_ml": water_cur,
            "target_ml": water_tgt,
        },
        "weight": {
            "today": weight_today,
            "stats": weight_stats,
        },
        "stats_7d": {
            "dates_7": dates_7,
            "dates": dates_7,
            "period": 7,
            "daily_focus": weekly_stats.get("daily", {}),
            "daily_completed": await habits_repo.get_daily_completed_counts(user_id, start_date_str, local_date),
            "habits_breakdown": weekly_stats.get("habits", []),
            "daily_calories": await nutrition_repo.get_weekly_nutrition_stats(user_id, start_date_str, local_date),
            "daily_water": await water_repo.get_weekly_water_stats(user_id, start_date_str, local_date),
            "daily_weight": await weight_repo.get_weight_range(user_id, start_date_str, local_date),
            "weight_stats": weight_stats,
            "nutrition_goals": nutr_goals,
            "water_target": water_tgt,
        },
    }
    return web.json_response({"ok": True, "data": data})


@routes.get("/api/stats")
async def api_stats(request: web.Request) -> web.Response:
    """Return productivity and health analytics for a requested period (7, 15, 30, 90 days)."""
    auth = request["auth"]
    user_id = auth["user_id"]
    local_date = auth["local_date"]

    try:
        period_days = int(request.query.get("period", "7"))
    except (ValueError, TypeError):
        period_days = 7

    if period_days not in (7, 15, 30, 90):
        period_days = 7

    end_dt = datetime.strptime(local_date, "%Y-%m-%d")
    dates = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(period_days - 1, -1, -1)]
    start_date_str = dates[0]

    habits_data = await habits_repo.get_weekly_stats(user_id, start_date_str, local_date)
    daily_completed = await habits_repo.get_daily_completed_counts(user_id, start_date_str, local_date)
    daily_calories = await nutrition_repo.get_weekly_nutrition_stats(user_id, start_date_str, local_date)
    daily_water = await water_repo.get_weekly_water_stats(user_id, start_date_str, local_date)
    daily_weight = await weight_repo.get_weight_range(user_id, start_date_str, local_date)
    weight_stats = await weight_repo.get_weight_stats(user_id, local_date)
    nutrition_goals = await nutrition_repo.get_nutrition_goals(user_id)
    water_target = await water_repo.get_water_target(user_id)

    return web.json_response(
        {
            "ok": True,
            "data": {
                "period": period_days,
                "dates": dates,
                "daily_focus": habits_data.get("daily", {}),
                "daily_completed": daily_completed,
                "habits_breakdown": habits_data.get("habits", []),
                "daily_calories": daily_calories,
                "daily_water": daily_water,
                "daily_weight": daily_weight,
                "weight_stats": weight_stats,
                "nutrition_goals": nutrition_goals,
                "water_target": water_target,
            },
        }
    )


@routes.patch("/api/settings")
async def api_update_settings(request: web.Request) -> web.Response:
    """Partially update user settings (timezone, checkin, reminders, mute) and sync chat message."""
    auth = request["auth"]
    user_id = auth["user_id"]
    bot = request.app["bot"]
    wid = request["wid"]

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    # 1. Timezone offset
    if "utc_offset" in body:
        try:
            offset = int(body["utc_offset"])
            if -12 <= offset <= 14:
                await users_repo.update_user_timezone(user_id, offset)
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "utc_offset must be an integer between -12 and 14"}, status=400)

    # 2. Check-in time
    if "checkin_time" in body:
        ci_time = str(body["checkin_time"]).strip()
        if re.match(r"^([0-1]?\d|2[0-3]):([0-5]\d)$", ci_time):
            await users_repo.update_user_checkin_time(user_id, ci_time)
        else:
            return web.json_response({"ok": False, "error": "checkin_time must be in HH:MM format"}, status=400)

    # 3. Weight reminder
    if "weight_reminder_enabled" in body or "weight_reminder_time" in body:
        w_enabled = int(bool(body.get("weight_reminder_enabled", False))) if "weight_reminder_enabled" in body else None
        w_time = str(body["weight_reminder_time"]).strip() if "weight_reminder_time" in body else None
        if w_time and not re.match(r"^([0-1]?\d|2[0-3]):([0-5]\d)$", w_time):
            return web.json_response({"ok": False, "error": "weight_reminder_time must be in HH:MM format"}, status=400)
        await users_repo.update_weight_reminder(user_id, enabled=w_enabled, reminder_time=w_time)

    # 4. Mute notifications
    if "mute_action" in body:
        action = str(body["mute_action"])
        now_ts = int(time.time())
        if action == "unmute":
            await users_repo.unmute_notifications(user_id)
        elif action == "forever":
            await users_repo.mute_notifications(user_id, 2147483647)
        elif action.isdigit() or isinstance(body["mute_action"], int):
            sec = int(body["mute_action"])
            await users_repo.mute_notifications(user_id, now_ts + sec)

    updated_user = await users_repo.get_user(user_id)
    synced = await sync_widget_message(bot, user_id, wid=wid, fallback_view="settings")

    return web.json_response({"ok": True, "data": updated_user, "synced": synced})
