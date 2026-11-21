"""Handler for focus timers: interactive session management, pause, resume, early stop, and reflection notes."""

import html
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database import habits_repo, timers_repo, users_repo
from bot.keyboards.inline import get_timer_controls_keyboard
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.widget_launcher import is_widget_enabled, send_widget_card
from bot.states.habit_states import TimerStates

logger = logging.getLogger(__name__)
router = Router(name="timer_router")


# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------

def calculate_timer_spent_seconds(timer: Dict[str, Any], now_ts: Optional[int] = None) -> int:
    """Calculate total active seconds spent in this session."""
    current_ts = now_ts if now_ts is not None else int(time.time())
    if timer.get("is_paused"):
        return timer.get("elapsed_before_pause", 0)
    return timer.get("elapsed_before_pause", 0) + max(0, current_ts - timer.get("started_at", current_ts))


async def render_timer_view(target_message: Message, user_id: int) -> None:
    """Render active timer status to target message using explicit user_id."""
    timer = await timers_repo.get_timer(user_id)
    if not timer:
        await target_message.answer(
            "⏱ У вас нет активных фокус-сессий.\n\n"
            "Выберите привычку в разделе «📋 Мои привычки» и запустите таймер!",
            reply_markup=get_main_reply_keyboard(),
        )
        return

    now_ts = int(time.time())
    spent_sec = calculate_timer_spent_seconds(timer, now_ts)
    spent_min = round(spent_sec / 60)
    target_min = round(timer["total_target_seconds"] / 60)
    remaining_min = max(0, target_min - spent_min)

    is_paused = bool(timer["is_paused"])
    status = "⏸ На паузе" if is_paused else "▶️ В процессе..."
    escaped_h_name = html.escape(timer["habit_name"])

    await target_message.answer(
        f"⏱ <b>Текущая фокус-сессия:</b>\n\n"
        f"🎯 Привычка: «{escaped_h_name}»\n"
        f"⏳ Прошло: <b>{spent_min} мин</b> | Осталось: <b>{remaining_min} мин</b>\n"
        f"📊 Статус: <b>{status}</b>",
        reply_markup=get_timer_controls_keyboard(is_paused=is_paused),
    )


# ---------------------------------------------------------
# Start Timer
# ---------------------------------------------------------

@router.callback_query(F.data.startswith("timer_start:"))
async def cb_start_timer(
    callback: CallbackQuery,
    local_date: str,
    local_weekday: int,
) -> None:
    """Start or replace focus session for the selected habit."""
    habit_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        await callback.answer("Привычка не найдена.", show_alert=True)
        return

    # Check if a timer is already running
    existing_timer = await timers_repo.get_timer(user_id)
    if existing_timer:
        await callback.answer(
            f"⚠️ У вас уже запущен таймер по привычке «{existing_timer['habit_name']}»! Завершите его перед новым.",
            show_alert=True,
        )
        return

    # Calculate remaining time today
    today_habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
    matching = next((h for h in today_habits if h["id"] == habit_id), None)
    spent_today = matching.get("spent_minutes", 0) if matching else 0
    target_minutes = habit["target_minutes"]
    remaining_min = max(0, target_minutes - spent_today)

    session_minutes = remaining_min if remaining_min > 0 else target_minutes
    target_seconds = session_minutes * 60
    now_ts = int(time.time())

    escaped_name = html.escape(habit["name"])
    # Send initial timer card message
    card_msg = await callback.message.answer(
        f"⏱ <b>Фокус-сессия запущена!</b>\n\n"
        f"🎯 Привычка: «{escaped_name}»\n"
        f"⏳ Запланировано: <b>{session_minutes} мин</b>\n"
        f"▶️ Статус: <b>В процессе...</b>\n\n"
        "Сконцентрируйтесь на задаче. Не отвлекайтесь!",
        reply_markup=get_timer_controls_keyboard(is_paused=False),
    )

    # Persist session to active_timers
    await timers_repo.create_or_replace_timer(
        user_id=user_id,
        habit_id=habit_id,
        total_target_seconds=target_seconds,
        message_id=card_msg.message_id,
        chat_id=chat_id,
        started_at=now_ts,
    )

    await callback.answer("Фокус-сессия началась! Удачи!")


# ---------------------------------------------------------
# Timer Controls: Pause, Resume, Add Time, Early Stop
# ---------------------------------------------------------

@router.callback_query(F.data == "timer_action:pause")
async def cb_timer_pause(callback: CallbackQuery) -> None:
    """Pause currently running timer."""
    user_id = callback.from_user.id
    now_ts = int(time.time())

    timer = await timers_repo.pause_timer(user_id, now_ts)
    if not timer:
        await callback.answer("Активный таймер не найден.", show_alert=True)
        return

    spent_sec = timer["elapsed_before_pause"]
    spent_min = round(spent_sec / 60)
    target_min = round(timer["total_target_seconds"] / 60)
    escaped_h_name = html.escape(timer["habit_name"])

    try:
        await callback.message.edit_text(
            f"⏸ <b>Фокус-сессия на паузе</b>\n\n"
            f"🎯 Привычка: «{escaped_h_name}»\n"
            f"⏱ Набежало: {spent_min}/{target_min} мин\n"
            f"⏸ Статус: <b>Приостановлена</b>\n\n"
            "Нажмите «Возобновить», когда вернетесь к задаче.",
            reply_markup=get_timer_controls_keyboard(is_paused=True),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning("TelegramBadRequest on timer pause: %s", e)
    except Exception as e:
        logger.debug("Failed to edit timer text on pause: %s", e)

    await callback.answer("Таймер приостановлен.")


@router.callback_query(F.data == "timer_action:resume")
async def cb_timer_resume(callback: CallbackQuery) -> None:
    """Resume paused timer."""
    user_id = callback.from_user.id
    now_ts = int(time.time())

    timer = await timers_repo.resume_timer(user_id, now_ts)
    if not timer:
        await callback.answer("Активный таймер не найден.", show_alert=True)
        return

    target_min = round(timer["total_target_seconds"] / 60)
    escaped_h_name = html.escape(timer["habit_name"])

    try:
        await callback.message.edit_text(
            f"⏱ <b>Фокус-сессия возобновлена!</b> ▶️\n\n"
            f"🎯 Привычка: «{escaped_h_name}»\n"
            f"⏳ Цель сессии: <b>{target_min} мин</b>\n"
            f"▶️ Статус: <b>В процессе...</b>\n\n"
            "Сконцентрируйтесь на задаче!",
            reply_markup=get_timer_controls_keyboard(is_paused=False),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning("TelegramBadRequest on timer resume: %s", e)
    except Exception as e:
        logger.debug("Failed to edit timer text on resume: %s", e)

    await callback.answer("Таймер возобновлен!")


@router.callback_query(F.data == "timer_action:add_10")
async def cb_timer_add_10(callback: CallbackQuery) -> None:
    """Add 10 minutes (+600 seconds) to active timer."""
    user_id = callback.from_user.id
    timer = await timers_repo.add_time_to_timer(user_id, additional_seconds=600)
    if not timer:
        await callback.answer("Активный таймер не найден.", show_alert=True)
        return

    target_min = round(timer["total_target_seconds"] / 60)
    is_paused = bool(timer["is_paused"])
    status_str = "Приостановлена" if is_paused else "В процессе..."
    escaped_h_name = html.escape(timer["habit_name"])

    try:
        await callback.message.edit_text(
            f"⏱ <b>Фокус-сессия продлена на +10 мин!</b>\n\n"
            f"🎯 Привычка: «{escaped_h_name}»\n"
            f"⏳ Новая цель сессии: <b>{target_min} мин</b>\n"
            f"▶️ Статус: <b>{status_str}</b>",
            reply_markup=get_timer_controls_keyboard(is_paused=is_paused),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning("TelegramBadRequest on timer add_10: %s", e)
    except Exception as e:
        logger.debug("Failed to edit timer text on add_10: %s", e)

    await callback.answer("Добавлено +10 минут к сессии!")


@router.callback_query(F.data == "timer_action:stop")
async def cb_timer_stop(
    callback: CallbackQuery,
    state: FSMContext,
    local_date: str,
) -> None:
    """Early stop of active timer and save progress atomically."""
    user_id = callback.from_user.id
    now_ts = int(time.time())

    # Atomically delete timer first to prevent race condition
    timer = await timers_repo.delete_timer(user_id)
    if not timer:
        await callback.answer("Активный таймер не найден или уже завершен.", show_alert=True)
        return

    # Calculate actual spent time
    spent_seconds = calculate_timer_spent_seconds(timer, now_ts)
    actual_minutes = round(spent_seconds / 60)
    if spent_seconds >= 30 and actual_minutes == 0:
        actual_minutes = 1

    habit_id = timer["habit_id"]
    habit_name = timer["habit_name"]
    escaped_habit_name = html.escape(habit_name)
    target_minutes = timer["target_minutes"]

    if actual_minutes > 0:
        spent_today, is_completed = await habits_repo.upsert_habit_log(
            habit_id=habit_id,
            user_id=user_id,
            logged_date=local_date,
            add_minutes=actual_minutes,
            target_minutes=target_minutes,
        )

        remaining = max(0, target_minutes - spent_today)
        completion_str = " 🏆 (Цель на сегодня достигнута!)" if is_completed else f" (Осталось: {remaining} мин)"

        try:
            await callback.message.edit_text(
                f"⏹ <b>Сессия завершена досрочно!</b>\n\n"
                f"🎯 Привычка: «{escaped_habit_name}»\n"
                f"⏱ Засчитано: <b>+{actual_minutes} мин.</b>\n"
                f"📊 Всего сегодня: <b>{spent_today}/{target_minutes} мин</b>{completion_str}",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                logger.warning("TelegramBadRequest on timer stop: %s", e)
        except Exception as e:
            logger.debug("Failed to edit timer text on stop: %s", e)

        # Set reflection state
        await state.set_state(TimerStates.waiting_for_reflection)
        await state.update_data(habit_id=habit_id, logged_date=local_date, habit_name=habit_name)

        await callback.message.answer(
            f"✍️ Напишите коротко, что успели сделать по «{escaped_habit_name}» (или нажмите /skip):"
        )
    else:
        try:
            await callback.message.edit_text(
                f"⏹ Фокус-сессия по «{escaped_habit_name}» отменена (прошло менее 30 сек, время не засчитано).",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                logger.warning("TelegramBadRequest on timer cancel: %s", e)
        except Exception as e:
            logger.debug("Failed to edit timer text on cancel: %s", e)

    await callback.answer("Сессия остановлена.")


# ---------------------------------------------------------
# Reflection Note Handlers
# ---------------------------------------------------------

@router.message(TimerStates.waiting_for_reflection, Command("skip"))
async def msg_skip_reflection(message: Message, state: FSMContext) -> None:
    """Skip reflection note input."""
    await state.clear()
    await message.answer(
        "👌 Принято! Сессия сохранена без заметки. Продолжайте в том же духе! 🚀",
        reply_markup=get_main_reply_keyboard(),
    )


@router.message(TimerStates.waiting_for_reflection)
async def msg_save_reflection(message: Message, state: FSMContext) -> None:
    """Save reflection note into habit_logs.notes."""
    note = (message.text or "").strip()
    data = await state.get_data()
    habit_id = data.get("habit_id")
    logged_date = data.get("logged_date")

    if habit_id and logged_date and note:
        await habits_repo.update_habit_log_notes(habit_id, logged_date, note)

    await state.clear()
    await message.answer(
        "📝 <b>Заметка успешно сохранена в отчет!</b>\nОтличная работа сегодня! 🔥",
        reply_markup=get_main_reply_keyboard(),
    )


# ---------------------------------------------------------
# Check Active Timer Status
# ---------------------------------------------------------

@router.message(F.text == "⏱ Активный таймер")
@router.message(Command("timer"))
async def cmd_check_timer(message: Message) -> None:
    """Display currently running or paused timer status."""
    if is_widget_enabled():
        user = await users_repo.get_user(message.from_user.id)
        offset = user.get("utc_offset", 3) if user else 3
        local_now = datetime.now(timezone.utc) + timedelta(hours=offset)
        await send_widget_card(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            view="timer",
            local_date=local_now.strftime("%Y-%m-%d"),
            local_weekday=local_now.isoweekday(),
            utc_offset=offset,
            reply_markup=get_main_reply_keyboard(),
        )
        return

    await render_timer_view(target_message=message, user_id=message.from_user.id)
