"""Background scheduler using APScheduler for timer checks, evening check-ins, and midnight resets."""

import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database import habits_repo, timers_repo, users_repo
from bot.keyboards.inline import get_checkin_dashboard_keyboard

logger = logging.getLogger(__name__)


async def check_active_timers(bot: Bot) -> None:
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

            # Calculate actual worked minutes
            actual_minutes = max(1, round(elapsed / 60))

            # Fetch user to get timezone and local date
            user = await users_repo.get_user(user_id)
            offset = user.get("utc_offset", 3) if user else 3
            user_local_now = datetime.now(timezone.utc) + timedelta(hours=offset)
            local_date = user_local_now.strftime("%Y-%m-%d")

            # Update habit log
            spent_today, is_completed = await habits_repo.upsert_habit_log(
                habit_id=habit_id,
                user_id=user_id,
                logged_date=local_date,
                add_minutes=actual_minutes,
                target_minutes=target_minutes,
            )

            # Remove timer record
            await timers_repo.delete_timer(user_id)

            # Edit existing timer message
            remaining = max(0, target_minutes - spent_today)
            completion_note = " 🏆 (Цель на сегодня достигнута!)" if is_completed else f" (Осталось: {remaining} мин)"

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"✅ **Сессия завершена по таймеру!**\n\n"
                        f"🎯 Привычка: «{habit_name}»\n"
                        f"⏱ Засчитано: {actual_minutes} мин.\n"
                        f"📊 Сегодня: {spent_today}/{target_minutes} мин.{completion_note}"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.debug("Could not edit timer message %s: %s", message_id, e)

            # Send sound notification and reflection prompt
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🔔 **Время вышло!** Фокус-сессия по «{habit_name}» успешно завершена!\n\n"
                        f"⏱ Добавлено: +{actual_minutes} мин.\n\n"
                        f"✍️ Напиши коротко, что успел сделать (или отправь /skip):"
                    ),
                    disable_notification=False,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Failed to send timer alert to user %s: %s", user_id, e)


async def check_evening_checkins(bot: Bot) -> None:
    """Trigger evening review dashboard for users at their configured checkin_time."""
    users = await users_repo.get_all_users()
    now_utc = datetime.now(timezone.utc)

    for user in users:
        now_ts = int(time.time())
        muted_until = user.get("notifications_muted_until", 0) or 0
        if muted_until > now_ts:
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        checkin_time = user.get("checkin_time", "21:00")
        local_now = now_utc + timedelta(hours=offset)
        local_hm = local_now.strftime("%H:%M")
        local_date = local_now.strftime("%Y-%m-%d")

        if local_hm == checkin_time and user.get("last_checkin_date") != local_date:
            weekday = local_now.isoweekday()
            habits = await habits_repo.get_today_habits_with_status(user_id, local_date, weekday)

            if habits:
                total_count = len(habits)
                completed_count = sum(1 for h in habits if h.get("is_completed"))
                frozen_count = sum(1 for h in habits if h.get("is_frozen_today"))

                text_lines = [
                    f"🌙 **Вечерний чек-ин продуктивности!**",
                    f"📅 Дата: {local_date}",
                    f"📊 Прогресс: {completed_count + frozen_count}/{total_count} выполнено/заморожено\n",
                ]

                for h in habits:
                    spent = h.get("spent_minutes", 0)
                    target = h["target_minutes"]
                    if h.get("is_completed"):
                        status_str = f"✅ «{h['name']}» — {spent}/{target} мин"
                    elif h.get("is_frozen_today"):
                        status_str = f"❄️ «{h['name']}» — Заморожено"
                    else:
                        remaining = max(0, target - spent)
                        status_str = f"⏳ «{h['name']}» — {spent}/{target} мин (осталось {remaining} мин)"
                    text_lines.append(status_str)

                text_lines.append("\nОтметьте оставшиеся привычки или завершите чек-ин:")
                kb = get_checkin_dashboard_keyboard(habits)

                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text="\n".join(text_lines),
                        reply_markup=kb,
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning("Failed to send checkin message to %s: %s", user_id, e)

            # Record checkin sent date
            await users_repo.update_user_last_checkin_date(user_id, local_date)


async def check_weight_reminders(bot: Bot) -> None:
    """Trigger morning weight reminder for users at their configured weight_reminder_time."""
    users = await users_repo.get_all_users()
    now_utc = datetime.now(timezone.utc)

    for user in users:
        if not user.get("weight_reminder_enabled"):
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        reminder_time = user.get("weight_reminder_time", "08:00")
        local_now = now_utc + timedelta(hours=offset)
        local_hm = local_now.strftime("%H:%M")
        local_date = local_now.strftime("%Y-%m-%d")

        if local_hm == reminder_time and user.get("last_weight_reminder_date") != local_date:
            try:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⚖️ Записать утренний вес", callback_data="weight_enter")],
                    ]
                )
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🌅 **Доброе утро! Время утреннего взвешивания.**\n\n"
                        "Встаньте на весы натощак после пробуждения и зафиксируйте результат, "
                        "чтобы отслеживать точную динамику веса."
                    ),
                    reply_markup=kb,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Failed to send weight reminder to %s: %s", user_id, e)

            await users_repo.update_user_last_weight_reminder_date(user_id, local_date)


async def check_midnight_resets() -> None:
    """Handle 00:00 streak calculations and weekly freeze resets for each user's local timezone."""
    users = await users_repo.get_all_users()
    now_utc = datetime.now(timezone.utc)

    for user in users:
        now_ts = int(time.time())
        muted_until = user.get("notifications_muted_until", 0) or 0
        if muted_until > now_ts:
            continue

        user_id = user["user_id"]
        offset = user.get("utc_offset", 3)
        local_now = now_utc + timedelta(hours=offset)
        local_date = local_now.strftime("%Y-%m-%d")

        # Check if local time is within midnight window (00:00 - 00:05) and not yet processed
        if local_now.hour == 0 and local_now.minute <= 5 and user.get("last_midnight_date") != local_date:
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


async def global_tick(bot: Bot) -> None:
    """Tick executed every 60 seconds by APScheduler."""
    try:
        await check_active_timers(bot)
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
        await check_midnight_resets()
    except Exception as e:
        logger.error("Error in midnight resets: %s", e, exc_info=True)


def init_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Initialize and start the AsyncIOScheduler instance."""
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(
        global_tick,
        "interval",
        seconds=60,
        args=[bot],
        id="global_bot_tick",
        replace_existing=True,
    )
    return scheduler
