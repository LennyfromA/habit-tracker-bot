"""Widget launcher, URL builder, chat card generator, and two-way message synchronization.

Designed for seamless transition between Telegram WebApp Info buttons and Bot API 10.4 native widgets.
"""

import html
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from bot.config import settings
from bot.database import (
    habits_repo,
    nutrition_repo,
    timers_repo,
    users_repo,
    water_repo,
    weight_repo,
    widgets_repo,
)
from bot.keyboards.inline import DAY_LABELS
from bot.utils.formatting import calculate_timer_spent_seconds, render_progress_bar

logger = logging.getLogger(__name__)

SUPPORTED_VIEWS = {
    "dashboard",
    "habits",
    "timer",
    "health",
    "checkin",
    "stats",
    "settings",
}


# ---------------------------------------------------------------------------
# Isolated Launch Factories (Bot API 10.4 Ready)
# ---------------------------------------------------------------------------

def is_widget_enabled() -> bool:
    """Return True if WIDGET_BASE_URL is configured and uses HTTPS."""
    url = (settings.WIDGET_BASE_URL or "").strip()
    return url.startswith("https://")


def build_widget_url(view: str, wid: str) -> str:
    """Safely build public HTTPS URL for launching the interactive widget."""
    base = settings.WIDGET_BASE_URL.rstrip("/")
    sanitized_view = view if view in SUPPORTED_VIEWS else "dashboard"
    return f"{base}/widget?view={sanitized_view}&wid={wid}"


def build_widget_launch_button(label: str, view: str, wid: str) -> InlineKeyboardButton:
    """
    Build main interactive launch button for the chat message.

    NOTE: Extension point for Bot API 10.4:
    When migrating to native Bot API 10.4 interactive message widgets,
    replace WebAppInfo with WidgetInfo or the corresponding Telegram API 10.4 widget descriptor.
    """
    url = build_widget_url(view, wid)
    return InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))


# ---------------------------------------------------------------------------
# HTML Chat Cards Builder
# ---------------------------------------------------------------------------

async def build_chat_card(
    user_id: int,
    view: str,
    wid: str,
    local_date: str,
    local_weekday: int,
    utc_offset: int,
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Build a concise, aesthetic HTML summary card and inline keyboard with a primary widget button.
    Keeps chat clean by summarizing multi-step operations into a single interactive message.
    """
    user = await users_repo.get_user(user_id)
    freezes_left = user.get("freezes_left", 0) if user else 0
    weekday_name = DAY_LABELS.get(local_weekday, "")

    view_type = view if view in SUPPORTED_VIEWS else "dashboard"

    if view_type == "habits":
        habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
        total = len(habits)
        completed = sum(1 for h in habits if h.get("is_completed"))
        frozen = sum(1 for h in habits if h.get("is_frozen_today"))

        lines = [
            f"📋 <b>План привычек на сегодня</b> ({weekday_name}, <code>{local_date}</code>)",
            f"Прогресс: <b>{completed + frozen}/{total}</b> закрыто | ❄️ Заморозок: <b>{freezes_left}</b>\n",
        ]

        if not habits:
            lines.append("<i>На сегодня нет запланированных привычек. Отличный повод отдохнуть или создать новую!</i>")
        else:
            for h in habits:
                h_name = html.escape(h["name"])
                is_comp = bool(h.get("is_completed"))
                is_frz = bool(h.get("is_frozen_today"))
                htype = h.get("habit_type", "duration")

                if is_frz:
                    status_icon = "❄️"
                    details = "Заморожено"
                elif is_comp:
                    status_icon = "✅"
                    details = "Выполнено"
                else:
                    status_icon = "⏳"
                    if htype == "checklist":
                        cur = h.get("completed_repeats", 0)
                        tgt = h.get("target_repeats", 1) or 1
                        details = f"{cur}/{tgt} раз"
                    else:
                        spent = h.get("spent_minutes", 0)
                        target = h.get("target_minutes", 30)
                        details = f"{spent}/{target} мин"

                lines.append(f"{status_icon} <b>{h_name}</b> — {details}")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("✨ Открыть привычки", "habits", wid)],
            ]
        )
        return "\n".join(lines), kb

    elif view_type == "timer":
        timer = await timers_repo.get_timer(user_id)
        if timer:
            now_ts = int(time.time())
            spent_sec = calculate_timer_spent_seconds(timer, now_ts)
            spent_min = round(spent_sec / 60)
            target_min = round(timer["total_target_seconds"] / 60)
            remaining_min = max(0, target_min - spent_min)
            h_name = html.escape(timer.get("habit_name", "Фокус-сессия"))

            status_label = "⏸ <b>На паузе</b>" if timer.get("is_paused") else "▶️ <b>В процессе</b>"
            bar = render_progress_bar(spent_min, target_min, length=8)

            text = (
                f"⏱ <b>Фокус-сессия активна</b>\n\n"
                f"🎯 Привычка: «<b>{h_name}</b>»\n"
                f"Статус: {status_label}\n"
                f"Прогресс: {bar} <b>{spent_min}/{target_min} мин</b> (Осталось: {remaining_min} мин)\n\n"
                "Нажмите кнопку ниже для управления сессией или паузы:"
            )
        else:
            text = (
                "⏱ <b>Таймер глубокого фокуса</b>\n\n"
                "В данный момент у вас нет запущенных сессий.\n"
                "Выберите задачу и погрузитесь в продуктивную работу без отвлечений!"
            )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("⏱ Управлять фокусом", "timer", wid)],
            ]
        )
        return text, kb

    elif view_type == "health":
        goals = await nutrition_repo.get_nutrition_goals(user_id)
        summary = await nutrition_repo.get_daily_summary(user_id, local_date)
        meals = await nutrition_repo.get_meals_for_date(user_id, local_date)
        water_cur = await water_repo.get_daily_water(user_id, local_date)
        water_tgt = await water_repo.get_water_target(user_id)
        w_record = await weight_repo.get_weight_for_date(user_id, local_date)

        target_cals = goals.get("target_calories", 2000)
        spent_cals = summary.get("total_calories", 0)
        cals_bar = render_progress_bar(spent_cals, target_cals, length=8)

        lines = [
            f"🍏 <b>Дневник здоровья на {local_date}</b>\n",
            f"🔥 <b>Калории:</b> {cals_bar} <b>{spent_cals}/{target_cals} ккал</b>",
        ]

        if goals.get("track_bju", 1):
            p = round(summary.get("total_protein", 0.0), 1)
            f = round(summary.get("total_fat", 0.0), 1)
            c = round(summary.get("total_carbs", 0.0), 1)
            lines.append(f"🥩 <b>БЖУ:</b> {p}г Б · {f}г Ж · {c}г У")

        if goals.get("track_water", 1):
            water_bar = render_progress_bar(water_cur, water_tgt, length=8)
            lines.append(f"💧 <b>Вода:</b> {water_bar} <b>{water_cur}/{water_tgt} мл</b>")

        if w_record:
            lines.append(f"⚖️ <b>Утренний вес:</b> <code>{w_record['weight']} кг</code>")

        lines.append(f"\n🍽 Приемов пищи сегодня: <b>{len(meals)}</b>")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("🍏 Открыть дневник здоровья", "health", wid)],
            ]
        )
        return "\n".join(lines), kb

    elif view_type == "checkin":
        habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
        total = len(habits)
        completed = sum(1 for h in habits if h.get("is_completed"))
        frozen = sum(1 for h in habits if h.get("is_frozen_today"))

        summary = await nutrition_repo.get_daily_summary(user_id, local_date)
        water_cur = await water_repo.get_daily_water(user_id, local_date)
        water_tgt = await water_repo.get_water_target(user_id)

        lines = [
            f"🌙 <b>Вечерний чек-ин продуктивности ({local_date})</b>\n",
            f"🎯 <b>Привычки:</b> <b>{completed + frozen}/{total}</b> выполнено/заморожено",
            f"🔥 <b>Калории:</b> <b>{summary.get('total_calories', 0)} ккал</b>",
            f"💧 <b>Вода:</b> <b>{water_cur}/{water_tgt} мл</b>\n",
            "Подведите итоги дня в 1 клик, отметьте выполненные задачи и зафиксируйте стрик!",
        ]

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("🌙 Открыть чек-ин", "checkin", wid)],
            ]
        )
        return "\n".join(lines), kb

    elif view_type == "stats":
        end_dt = datetime.strptime(local_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=6)
        start_date_str = start_dt.strftime("%Y-%m-%d")

        stats_data = await habits_repo.get_weekly_stats(user_id, start_date_str, local_date)
        daily_stats = stats_data["daily"]
        habits_stats = stats_data["habits"]

        total_week_min = sum(daily_stats.values())
        hours = total_week_min // 60
        mins = total_week_min % 60
        top_streak = max((h.get("streak_days", 0) for h in habits_stats), default=0)

        text = (
            f"📊 <b>Статистика за 7 дней</b> ({start_dt.strftime('%d.%m')} — {end_dt.strftime('%d.%m')})\n\n"
            f"⏱ <b>Время в фокусе:</b> <b>{hours} ч {mins} мин</b> ({total_week_min} мин)\n"
            f"📅 <b>Активных дней:</b> <b>{len(daily_stats)} из 7</b>\n"
            f"🔥 <b>Лучший стрик:</b> <b>{top_streak} дн.</b>\n"
            f"🎯 <b>Привычек на контроле:</b> <b>{len(habits_stats)}</b>\n\n"
            "Интерактивные графики и подробная аналитика доступны в виджете:"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("📊 Интерактивная статистика", "stats", wid)],
                [InlineKeyboardButton(text="📁 Экспорт в .md", callback_data="settings_export")],
            ]
        )
        return text, kb

    elif view_type == "settings":
        offset = user.get("utc_offset", 3) if user else 3
        ci_time = user.get("checkin_time", "21:00") if user else "21:00"
        weight_enabled = bool(user.get("weight_reminder_enabled", 0)) if user else False
        weight_time = user.get("weight_reminder_time", "08:00") if user else "08:00"
        w_status = f"{weight_time} (Вкл)" if weight_enabled else "Выключено"

        now_ts = int(time.time())
        muted_until = (user.get("notifications_muted_until", 0) or 0) if user else 0
        if muted_until > now_ts:
            if muted_until >= 2000000000:
                notif_status = "🔕 Отключены навсегда"
            else:
                until_dt = datetime.fromtimestamp(muted_until, timezone.utc) + timedelta(hours=offset)
                notif_status = f"🔕 До {until_dt.strftime('%d.%m %H:%M')}"
        else:
            notif_status = "🔔 Активны"

        text = (
            "⚙️ <b>Параметры и настройки бота</b>\n\n"
            f"🌍 Часовой пояс: <b>UTC{offset:+d}</b>\n"
            f"🌙 Вечерний чек-ин: <b>{ci_time}</b>\n"
            f"⚖️ Утреннее взвешивание: <b>{w_status}</b>\n"
            f"❄️ Заморозок в неделю: <b>{freezes_left}</b>\n"
            f"🔔 Уведомления: <b>{notif_status}</b>\n\n"
            "Настройте расписание, цели и уведомления в интерактивном окне:"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("⚙️ Открыть настройки", "settings", wid)],
            ]
        )
        return text, kb

    else:
        # view_type == "dashboard" (unified day panel)
        habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
        total_habits = len(habits)
        completed_habits = sum(1 for h in habits if h.get("is_completed"))
        frozen_habits = sum(1 for h in habits if h.get("is_frozen_today"))

        goals = await nutrition_repo.get_nutrition_goals(user_id)
        summary = await nutrition_repo.get_daily_summary(user_id, local_date)
        water_cur = await water_repo.get_daily_water(user_id, local_date)
        water_tgt = await water_repo.get_water_target(user_id)

        target_cals = goals.get("target_calories", 2000)
        spent_cals = summary.get("total_calories", 0)

        timer = await timers_repo.get_timer(user_id)
        timer_status = "⏱ Фокус-сессия активна" if timer else "⏱ Нет активных таймеров"

        habits_bar = render_progress_bar(completed_habits + frozen_habits, total_habits or 1, length=8)
        cals_bar = render_progress_bar(spent_cals, target_cals, length=8)
        water_bar = render_progress_bar(water_cur, water_tgt, length=8)

        text = (
            f"✨ <b>Главная панель продуктивности</b>\n"
            f"📅 {weekday_name}, <code>{local_date}</code>\n\n"
            f"🎯 <b>Привычки:</b> {habits_bar} <b>{completed_habits + frozen_habits}/{total_habits}</b>\n"
            f"🔥 <b>Калории:</b> {cals_bar} <b>{spent_cals}/{target_cals} ккал</b>\n"
            f"💧 <b>Вода:</b> {water_bar} <b>{water_cur}/{water_tgt} мл</b>\n"
            f"{timer_status}\n\n"
            "Откройте интерактивную панель для полного контроля дня:"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [build_widget_launch_button("✨ Открыть панель дня", "dashboard", wid)],
            ]
        )
        return text, kb


# ---------------------------------------------------------------------------
# Message Dispatch & Two-Way Synchronization
# ---------------------------------------------------------------------------

async def send_widget_card(
    target_message: Optional[Message] = None,
    user_id: Optional[int] = None,
    view: str = "dashboard",
    local_date: Optional[str] = None,
    local_weekday: Optional[int] = None,
    utc_offset: int = 3,
    bot: Optional[Bot] = None,
    chat_id: Optional[int] = None,
    reply_markup: Optional[Any] = None,
    **kwargs: Any,
) -> Message:
    """
    Send widget chat card, store binding in database, and return sent Message.
    Supports both direct Bot.send_message(chat_id, ...) and target_message.answer(...) calling conventions.
    """
    if target_message is not None:
        if bot is None:
            bot = target_message.bot
        if chat_id is None:
            chat_id = target_message.chat.id
        if user_id is None and target_message.from_user:
            user_id = target_message.from_user.id

    if bot is None or chat_id is None:
        raise ValueError("Either target_message or both bot and chat_id must be provided to send_widget_card")

    if user_id is None:
        user_id = chat_id

    # Ensure user exists in database to satisfy foreign key constraints
    existing_user = await users_repo.get_user(user_id)
    if not existing_user:
        await users_repo.create_or_update_user(
            user_id=user_id,
            username="",
            utc_offset=utc_offset,
            checkin_time="21:00",
        )

    if local_date is None or local_weekday is None:
        now_utc = datetime.now(timezone.utc)
        local_now = now_utc + timedelta(hours=utc_offset)
        if local_date is None:
            local_date = local_now.strftime("%Y-%m-%d")
        if local_weekday is None:
            local_weekday = local_now.isoweekday()

    wid = secrets.token_urlsafe(9)
    text, kb = await build_chat_card(
        user_id=user_id,
        view=view,
        wid=wid,
        local_date=local_date,
        local_weekday=local_weekday,
        utc_offset=utc_offset,
    )

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        parse_mode="HTML",
    )
    await widgets_repo.save_widget_message(
        wid=wid,
        user_id=user_id,
        chat_id=chat_id,
        message_id=sent.message_id,
        view_type=view,
    )
    return sent


async def send_widget_push(
    bot: Bot,
    chat_id: int,
    user_id: int,
    view: str,
    local_date: str,
    local_weekday: int,
    utc_offset: int,
    prefix_html: Optional[str] = None,
) -> Message:
    """Send scheduled push notification as an interactive widget card."""
    wid = secrets.token_urlsafe(9)
    card_text, kb = await build_chat_card(
        user_id=user_id,
        view=view,
        wid=wid,
        local_date=local_date,
        local_weekday=local_weekday,
        utc_offset=utc_offset,
    )

    full_text = f"{prefix_html}\n\n{card_text}" if prefix_html else card_text
    sent = await bot.send_message(
        chat_id=chat_id,
        text=full_text,
        reply_markup=kb,
        parse_mode="HTML",
    )
    await widgets_repo.save_widget_message(
        wid=wid,
        user_id=user_id,
        chat_id=chat_id,
        message_id=sent.message_id,
        view_type=view,
    )
    return sent


async def sync_widget_message(
    bot: Bot,
    user_id: int,
    wid: Optional[str] = None,
    fallback_view: Optional[str] = None,
) -> bool:
    """
    Synchronize the Telegram chat message linked to this widget.
    Gracefully handles TelegramBadRequest if message is unchanged, returns False if message is missing/deleted.
    """
    binding = None
    if wid:
        binding = await widgets_repo.get_widget_message(wid, user_id)

    if not binding and fallback_view:
        binding = await widgets_repo.get_latest_widget_message(user_id, view_type=fallback_view)

    if not binding:
        # Fallback to the latest widget message of any view
        binding = await widgets_repo.get_latest_widget_message(user_id)

    if not binding:
        logger.debug("No widget message binding found for user_id=%s, wid=%s", user_id, wid)
        return False

    user = await users_repo.get_user(user_id)
    utc_offset = user.get("utc_offset", 3) if user else 3
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc + timedelta(hours=utc_offset)
    local_date = local_now.strftime("%Y-%m-%d")
    local_weekday = local_now.isoweekday()

    actual_wid = binding["wid"]
    view_type = binding.get("view_type", "dashboard")
    chat_id = binding["chat_id"]
    message_id = binding["message_id"]

    try:
        new_text, new_kb = await build_chat_card(
            user_id=user_id,
            view=view_type,
            wid=actual_wid,
            local_date=local_date,
            local_weekday=local_weekday,
            utc_offset=utc_offset,
        )

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=new_text,
            reply_markup=new_kb,
            parse_mode="HTML",
        )
        await widgets_repo.touch_widget_message(actual_wid)
        return True
    except TelegramBadRequest as e:
        err_msg = str(e).lower()
        if "message is not modified" in err_msg:
            # Content hasn't changed; treat as success
            await widgets_repo.touch_widget_message(actual_wid)
            return True
        elif "message to edit not found" in err_msg or "message can't be edited" in err_msg:
            logger.debug("Widget message %s cannot be edited: %s", message_id, e)
            return False
        else:
            logger.warning("TelegramBadRequest during widget sync for user %s: %s", user_id, e)
            return False
    except Exception as e:
        logger.warning("Unexpected error during widget sync for user %s: %s", user_id, e)
        return False
