"""Background scheduler using APScheduler for timer checks, evening check-ins, and midnight resets."""

import html
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database import habits_repo, nutrition_repo, tasks_repo, timers_repo, users_repo, water_repo, widgets_repo
from bot.handlers.checkin import format_checkin_text
from bot.keyboards.inline import get_checkin_dashboard_keyboard, get_habit_reminder_push_keyboard
from bot.services.widget_launcher import is_widget_enabled, send_widget_push, sync_widget_message
from bot.states.habit_states import TimerStates

logger = logging.getLogger(__name__)


def is_time_in_window(current_hm: str, target_hm: str, window_minutes: int = 30) -> bool:
    """Return True if current_hm is within [target_hm, target_hm + window_minutes].
    Handles single hour format e.g. '7' or '07' as '07:00'.
    """
    try:
        cur_h, cur_m = map(int, current_hm.split(":"))
        if ":" in target_hm:
            tgt_h, tgt_m = map(int, target_hm.split(":"))
        else:
            tgt_h, tgt_m = int(target_hm), 0
        diff = (cur_h * 60 + cur_m) - (tgt_h * 60 + tgt_m)
        return 0 <= diff <= window_minutes
    except Exception:
        return current_hm == target_hm


async def check_active_timers(bot: Bot, storage: Optional[BaseStorage] = None) -> None:
    """Check running focus sessions and complete those whose target duration has elapsed."""
    running_timers = await timers_repo.get_all_running_timers()
    now_ts = int(time.time())

    for timer in running_timers:
        elapsed = timer["elapsed_before_pause"] + max(0, now_ts - timer["started_at"])
        if elapsed >= timer["total_target_seconds"]:
            user_id = timer["user_id"]
            habit_id = timer["habit_id"]
            chat_id = timer["chat_id"]
            message_id = timer["message_id"]
            habit_name = timer["habit_name"]
            target_minutes = timer["target_minutes"]

            # 1. Atomically delete timer first to prevent race condition with manual stop
            deleted_timer = await timers_repo.delete_timer(user_id)
            if not deleted_timer:
                continue  # Timer was already stopped concurrently

            actual_minutes = max(1, round(elapsed / 60))

            # Check if this timer was for a task
            if deleted_timer.get("task_id") or deleted_timer.get("target_type") == "task":
                task_id = deleted_timer["task_id"]
                task_name = deleted_timer.get("target_name") or deleted_timer.get("habit_name") or "Задача"
                escaped_task_name = html.escape(task_name)
                await tasks_repo.add_task_spent_minutes(task_id, actual_minutes)

                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"✅ <b>Сессия завершена по таймеру!</b>\n\n"
                            f"🎯 Задача: «{escaped_task_name}»\n"
                            f"⏱ Засчитано: {actual_minutes} мин."
                        ),
                    )
                except Exception as e:
                    logger.debug("Could not edit task timer message %s: %s", message_id, e)

                if is_widget_enabled():
                    try:
                        latest_w = await widgets_repo.get_latest_widget_message(user_id)
                        if latest_w:
                            await sync_widget_message(bot, user_id, latest_w["wid"], latest_w["view_type"])
                    except Exception as e:
                        logger.debug("Failed to sync widget message on task timer auto-completion: %s", e)

                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Отметить задачу выполненной",
                                callback_data=f"task_rem_done:{task_id}",
                            )
                        ]
                    ]
                )
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🔔 <b>Время вышло!</b> Фокус-сессия по задаче «{escaped_task_name}» успешно завершена!\n\n"
                            f"⏱ Засчитано: +{actual_minutes} мин."
                        ),
                        reply_markup=kb,
                        disable_notification=False,
                    )
                except Exception as e:
                    logger.warning("Failed to send task timer alert to user %s: %s", user_id, e)
                continue

            # Fetch user to get timezone and local date
            user = await users_repo.get_user(user_id)
            offset = user.get("utc_offset", 3) if user else 3
            user_local_now = datetime.now(timezone.utc) + timedelta(hours=offset)
            local_date = user_local_now.strftime("%Y-%m-%d")

            # 2. Update habit log
            spent_today, is_completed = await habits_repo.upsert_habit_log(
                habit_id=habit_id,
                user_id=user_id,
                logged_date=local_date,
                add_minutes=actual_minutes,
                target_minutes=target_minutes,
            )

            # 3. Edit existing timer card message
            remaining = max(0, target_minutes - spent_today)
            completion_note = " 🏆 (Цель на сегодня достигнута!)" if is_completed else f" (Осталось: {remaining} мин)"

            escaped_habit_name = html.escape(habit_name)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"✅ <b>Сессия завершена по таймеру!</b>\n\n"
                        f"🎯 Привычка: «{escaped_habit_name}»\n"
                        f"⏱ Засчитано: {actual_minutes} мин.\n"
                        f"📊 Сегодня: {spent_today}/{target_minutes} мин.{completion_note}"
                    ),
                )
            except Exception as e:
                logger.debug("Could not edit timer message %s: %s", message_id, e)

            # Sync widget cards if active
            if is_widget_enabled():
                try:
                    latest_w = await widgets_repo.get_latest_widget_message(user_id)
                    if latest_w:
                        await sync_widget_message(bot, user_id, latest_w["wid"], latest_w["view_type"])
                except Exception as e:
                    logger.debug("Failed to sync widget message on timer auto-completion: %s", e)

            # 4. Activate FSM state for reflection note
            if storage:
                fsm_ctx = FSMContext(
                    storage=storage,
                    key=StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=user_id),
                )
                await fsm_ctx.set_state(TimerStates.waiting_for_reflection)
                await fsm_ctx.update_data(habit_id=habit_id, logged_date=local_date, habit_name=habit_name)

            # 5. Send notification and reflection prompt
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🔔 <b>Время вышло!</b> Фокус-сессия по «{escaped_habit_name}» успешно завершена!\n\n"
                        f"⏱ Добавлено: +{actual_minutes} мин.\n\n"
                        f"✍️ Напиши коротко, что успел сделать (или отправь /skip):"
                    ),
                    disable_notification=False,
                )
            except Exception as e:
                logger.warning("Failed to send timer alert to user %s: %s", user_id, e)


async def check_evening_checkins(bot: Bot) -> None:
    """Trigger evening review dashboard for users at their configured checkin_time window."""
    users = await users_repo.get_all_users()
    now_utc = datetime.now(timezone.utc)
    now_ts = int(time.time())

    for user in users:
        muted_until = user.get("notifications_muted_until", 0) or 0
        if muted_until > now_ts:
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        checkin_time = user.get("checkin_time", "21:00")
        local_now = now_utc + timedelta(hours=offset)
        local_hm = local_now.strftime("%H:%M")
        local_date = local_now.strftime("%Y-%m-%d")

        if is_time_in_window(local_hm, checkin_time, 30) and user.get("last_checkin_date") != local_date:
            weekday = local_now.isoweekday()
            habits = await habits_repo.get_today_habits_with_status(user_id, local_date, weekday)
            today_tasks = await tasks_repo.get_today_tasks(user_id, local_date)

            if habits or today_tasks:
                nutr_summary = await nutrition_repo.get_daily_summary(user_id, local_date)
                nutr_goals = await nutrition_repo.get_nutrition_goals(user_id)
                water_cur = await water_repo.get_daily_water(user_id, local_date)
                water_tgt = await water_repo.get_water_target(user_id)
                has_uncompleted = any(not t.get("is_completed") for t in today_tasks)

                checkin_text = format_checkin_text(
                    habits=habits,
                    local_date=local_date,
                    nutrition_summary=nutr_summary,
                    nutrition_goals=nutr_goals,
                    water_ml=water_cur,
                    water_target=water_tgt,
                    tasks=today_tasks,
                )
                kb = get_checkin_dashboard_keyboard(habits, has_uncompleted_tasks=has_uncompleted)

                try:
                    if is_widget_enabled():
                        await send_widget_push(
                            bot=bot,
                            user_id=user_id,
                            view="checkin",
                            title="🌙 Вечерний чек-ин",
                            body=checkin_text,
                            local_date=local_date,
                            local_weekday=weekday,
                            utc_offset=offset,
                        )
                    else:
                        await bot.send_message(
                            chat_id=user_id,
                            text=checkin_text,
                            reply_markup=kb,
                        )
                except Exception as e:
                    logger.warning("Failed to send checkin message to %s: %s", user_id, e)

            # Record checkin sent date
            await users_repo.update_user_last_checkin_date(user_id, local_date)


async def check_weight_reminders(bot: Bot) -> None:
    """Trigger morning weight reminder for users within reminder_time window."""
    users = await users_repo.get_all_users()
    now_utc = datetime.now(timezone.utc)
    now_ts = int(time.time())

    for user in users:
        if not user.get("weight_reminder_enabled"):
            continue

        # Honor notifications mute setting for weight reminders
        muted_until = user.get("notifications_muted_until", 0) or 0
        if muted_until > now_ts:
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        reminder_time = user.get("weight_reminder_time", "08:00")
        local_now = now_utc + timedelta(hours=offset)
        local_hm = local_now.strftime("%H:%M")
        local_date = local_now.strftime("%Y-%m-%d")

        if is_time_in_window(local_hm, reminder_time, 30) and user.get("last_weight_reminder_date") != local_date:
            try:
                weight_body = (
                    "🌅 <b>Доброе утро! Время утреннего взвешивания.</b>\n\n"
                    "Встаньте на весы натощак после пробуждения и зафиксируйте результат, "
                    "чтобы отслеживать точную динамику веса."
                )
                if is_widget_enabled():
                    await send_widget_push(
                        bot=bot,
                        user_id=user_id,
                        view="health",
                        title="⚖️ Утреннее взвешивание",
                        body=weight_body,
                        local_date=local_date,
                        local_weekday=local_now.isoweekday(),
                        utc_offset=offset,
                    )
                else:
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="⚖️ Записать утренний вес", callback_data="weight_enter")],
                        ]
                    )
                    await bot.send_message(
                        chat_id=user_id,
                        text=weight_body,
                        reply_markup=kb,
                    )
            except Exception as e:
                logger.warning("Failed to send weight reminder to %s: %s", user_id, e)

            await users_repo.update_user_last_weight_reminder_date(user_id, local_date)


async def check_midnight_resets() -> None:
    """
    Handle midnight streak calculations and weekly freeze resets for each user's local timezone.
    Always executes regardless of notifications_muted_until.
    Cleans up old records from habit_reminders_log.
    """
    users = await users_repo.get_all_users()
    now_utc = datetime.now(timezone.utc)

    for user in users:
        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        local_now = now_utc + timedelta(hours=offset)
        local_date = local_now.strftime("%Y-%m-%d")

        # Process if day has changed since last processed midnight
        if user.get("last_midnight_date") != local_date:
            yesterday_dt = local_now - timedelta(days=1)
            yesterday_date = yesterday_dt.strftime("%Y-%m-%d")
            yesterday_weekday = yesterday_dt.isoweekday()

            # Process streaks based on yesterday's completion and freeze status
            await habits_repo.midnight_process_streaks_for_user(
                user_id=user_id,
                yesterday_date=yesterday_date,
                yesterday_weekday=yesterday_weekday,
            )

            # Reset streak freezes if today is Monday (weekday == 1)
            if local_now.isoweekday() == 1:
                await users_repo.reset_weekly_freezes(user_id)

            await users_repo.update_user_last_midnight_date(user_id, local_date)

    # Clean up reminder logs older than 14 days and widget messages older than 30 days
    cutoff_date = (now_utc - timedelta(days=14)).strftime("%Y-%m-%d")
    try:
        await habits_repo.cleanup_old_reminders_log(cutoff_date)
        await tasks_repo.cleanup_old_task_reminders(cutoff_date)
        await widgets_repo.cleanup_old_widget_messages(days=30)
    except Exception as e:
        logger.debug("Failed to clean up old reminders log or widget messages: %s", e)


async def check_habit_reminders(bot: Bot) -> None:
    """Send habit reminder pushes and trigger due snoozed notifications."""
    now_ts = int(time.time())
    now_utc = datetime.now(timezone.utc)

    # 1. Process due snoozes
    due_snoozes = await habits_repo.get_due_snoozes(now_ts)
    for snooze in due_snoozes:
        user_id = snooze["user_id"]
        habit_id = snooze["habit_id"]
        name = html.escape(snooze["name"])
        snooze_id = snooze["snooze_id"]

        user = await users_repo.get_user(user_id)
        muted_until = (user or {}).get("notifications_muted_until", 0) or 0
        if muted_until <= now_ts:
            try:
                kb = get_habit_reminder_push_keyboard(habit_id)
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⏰ <b>Напоминание (после откладывания):</b> «{name}»!\n\n"
                        f"Время выполнить действие и подтвердить результат."
                    ),
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning("Failed to send snoozed reminder to %s: %s", user_id, e)

        await habits_repo.delete_snooze(snooze_id)

    # 2. Process scheduled habit reminders
    users = await users_repo.get_all_users()
    for user in users:
        muted_until = user.get("notifications_muted_until", 0) or 0
        if muted_until > now_ts:
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        local_now = now_utc + timedelta(hours=offset)
        local_hm = local_now.strftime("%H:%M")
        local_date = local_now.strftime("%Y-%m-%d")
        weekday = local_now.isoweekday()

        habits = await habits_repo.get_today_habits_with_status(user_id, local_date, weekday)
        for h in habits:
            if h.get("habit_type") != "checklist":
                continue
            rem_str = h.get("reminder_times")
            if not rem_str:
                continue

            scheduled_times = [t.strip() for t in rem_str.split(",") if t.strip()]
            for rem_time in scheduled_times:
                if is_time_in_window(local_hm, rem_time, 30):
                    already_sent = await habits_repo.was_habit_reminder_sent(h["id"], local_date, rem_time)
                    if not already_sent:
                        cur_reps = h.get("completed_repeats", 0)
                        tgt_reps = h.get("target_repeats", 1) or 1
                        escaped_h_name = html.escape(h["name"])
                        try:
                            kb = get_habit_reminder_push_keyboard(h["id"])
                            await bot.send_message(
                                chat_id=user_id,
                                text=(
                                    f"🔔 <b>Напоминание по привычке:</b> «{escaped_h_name}»!\n\n"
                                    f"📊 Прогресс за сегодня: <b>{cur_reps}/{tgt_reps}</b>\n\n"
                                    f"Не забудьте выполнить действие!"
                                ),
                                reply_markup=kb,
                            )
                        except Exception as e:
                            logger.warning("Failed to send habit reminder to %s: %s", user_id, e)

                        await habits_repo.record_habit_reminder_sent(h["id"], user_id, local_date, rem_time)


async def check_task_reminders(bot: Bot) -> None:
    """Send due task reminder pushes to users with interactive action buttons."""
    now_ts = int(time.time())
    now_utc = datetime.now(timezone.utc)
    users = await users_repo.get_all_users()

    for user in users:
        muted_until = user.get("notifications_muted_until", 0) or 0
        if muted_until > now_ts or muted_until == 2147483647:
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        user_local_now = now_utc + timedelta(hours=offset)
        local_date = user_local_now.strftime("%Y-%m-%d")
        local_time = user_local_now.strftime("%H:%M")

        candidates = await tasks_repo.get_due_task_reminders_candidates(user_id, local_date, local_time)
        for task in candidates:
            # Check if reminder was already sent for this task today
            task_due_time = task.get("due_time") or local_time
            if await tasks_repo.was_task_reminder_sent(task["id"], local_date, task_due_time):
                continue

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Готово", callback_data=f"task_rem_done:{task['id']}"),
                        InlineKeyboardButton(text="📅 На завтра", callback_data=f"task_rem_tomorrow:{task['id']}"),
                    ]
                ]
            )
            dur_text = f" (~{task['estimated_minutes']} мин)" if task.get("estimated_minutes") else ""
            list_badge = f"{task.get('list_icon', '📁')} {task.get('list_name', 'Входящие')}"
            text = (
                f"⏰ <b>Напоминание о задаче!</b>\n\n"
                f"<b>«{html.escape(task['title'])}»</b>{dur_text}\n"
                f"📂 {html.escape(list_badge)}"
            )
            try:
                await bot.send_message(chat_id=user_id, text=text, reply_markup=kb)
            except Exception as e:
                logger.warning("Failed to send task reminder to %s: %s", user_id, e)

            await tasks_repo.record_task_reminder_sent(task["id"], user_id, local_date, task_due_time)


async def global_tick(bot: Bot, storage: Optional[BaseStorage] = None) -> None:
    """Tick executed every 60 seconds by APScheduler."""
    try:
        await check_active_timers(bot, storage)
    except Exception as e:
        logger.error("Error checking active timers: %s", e, exc_info=True)

    try:
        await check_evening_checkins(bot)
    except Exception as e:
        logger.error("Error in evening checkins: %s", e, exc_info=True)

    try:
        await check_weight_reminders(bot)
    except Exception as e:
        logger.error("Error in weight reminders: %s", e, exc_info=True)

    try:
        await check_habit_reminders(bot)
    except Exception as e:
        logger.error("Error in habit reminders: %s", e, exc_info=True)

    try:
        await check_task_reminders(bot)
    except Exception as e:
        logger.error("Error in task reminders: %s", e, exc_info=True)

    try:
        await check_midnight_resets()
    except Exception as e:
        logger.error("Error in midnight resets: %s", e, exc_info=True)


def init_scheduler(bot: Bot, storage: Optional[BaseStorage] = None) -> AsyncIOScheduler:
    """Initialize and start the AsyncIOScheduler instance."""
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(
        global_tick,
        "interval",
        seconds=60,
        args=[bot, storage],
        id="global_bot_tick",
        replace_existing=True,
    )
    return scheduler
