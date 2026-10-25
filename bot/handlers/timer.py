"""Handler for focus timers: interactive session management, pause, resume, early stop, and reflection notes."""

import logging
import time
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database import habits_repo, timers_repo
from bot.keyboards.inline import get_timer_controls_keyboard
from bot.keyboards.reply import get_main_reply_keyboard
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

    # Send initial timer card message
    card_msg = await callback.message.answer(
        f"⏱ **Фокус-сессия запущена!**\n\n"
        f"🎯 Привычка: «{habit['name']}»\n"
        f"⏳ Запланировано: **{session_minutes} мин**\n"
        f"▶️ Статус: **В процессе...**\n\n"
        "Сконцентрируйтесь на задаче. Не отвлекайтесь!",
        reply_markup=get_timer_controls_keyboard(is_paused=False),
        parse_mode="Markdown",
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

    try:
        await callback.message.edit_text(
            f"⏸ **Фокус-сессия на паузе**\n\n"
            f"🎯 Привычка: «{timer['habit_name']}»\n"
            f"⏱ Набежало: {spent_min}/{target_min} мин\n"
            f"⏸ Статус: **Приостановлена**\n\n"
            "Нажмите «Возобновить», когда вернетесь к задаче.",
            reply_markup=get_timer_controls_keyboard(is_paused=True),
            parse_mode="Markdown",
        )
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

    try:
        await callback.message.edit_text(
            f"⏱ **Фокус-сессия возобновлена!** ▶️\n\n"
            f"🎯 Привычка: «{timer['habit_name']}»\n"
            f"⏳ Цель сессии: **{target_min} мин**\n"
            f"▶️ Статус: **В процессе...**\n\n"
            "Сконцентрируйтесь на задаче!",
            reply_markup=get_timer_controls_keyboard(is_paused=False),
            parse_mode="Markdown",
        )
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

    try:
        await callback.message.edit_text(
            f"⏱ **Фокус-сессия продлена на +10 мин!**\n\n"
            f"🎯 Привычка: «{timer['habit_name']}»\n"
            f"⏳ Новая цель сессии: **{target_min} мин**\n"
            f"▶️ Статус: **{status_str}**",
            reply_markup=get_timer_controls_keyboard(is_paused=is_paused),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.debug("Failed to edit timer text on add_10: %s", e)

    await callback.answer("Добавлено +10 минут к сессии!")


@router.callback_query(F.data == "timer_action:stop")
async def cb_timer_stop(
    callback: CallbackQuery,
    state: FSMContext,
    local_date: str,
) -> None:
    """Early stop of active timer and save progress."""
    user_id = callback.from_user.id
    now_ts = int(time.time())

    timer = await timers_repo.get_timer(user_id)
    if not timer:
        await callback.answer("Активный таймер не найден.", show_alert=True)
        return

    # Calculate actual spent time
    spent_seconds = calculate_timer_spent_seconds(timer, now_ts)
    actual_minutes = round(spent_seconds / 60)
    if spent_seconds >= 30 and actual_minutes == 0:
        actual_minutes = 1

    # Remove timer from DB
    await timers_repo.delete_timer(user_id)

    habit_id = timer["habit_id"]
    habit_name = timer["habit_name"]
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
                f"⏹ **Сессия завершена досрочно!**\n\n"
                f"🎯 Привычка: «{habit_name}»\n"
                f"⏱ Засчитано: **+{actual_minutes} мин.**\n"
                f"📊 Всего сегодня: **{spent_today}/{target_minutes} мин**{completion_str}",
                reply_markup=None,
                parse_mode="Markdown",
            )
        except Exception:
            pass

        # Set reflection state
        await state.set_state(TimerStates.waiting_for_reflection)
        await state.update_data(habit_id=habit_id, logged_date=local_date, habit_name=habit_name)

        await callback.message.answer(
            f"✍️ Напишите коротко, что успели сделать по «{habit_name}» (или нажмите /skip):",
            parse_mode="Markdown",
        )
    else:
        try:
            await callback.message.edit_text(
                f"⏹ Фокус-сессия по «{habit_name}» отменена (прошло менее 30 сек, время не засчитано).",
                reply_markup=None,
            )
        except Exception:
            pass

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
        "📝 **Заметка успешно сохранена в отчет!**\nОтличная работа сегодня! 🔥",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------
# Check Active Timer Status
# ---------------------------------------------------------

@router.message(F.text == "⏱ Активный таймер")
@router.message(Command("timer"))
async def cmd_check_timer(message: Message) -> None:
    """Display currently running or paused timer status."""
    user_id = message.from_user.id
    timer = await timers_repo.get_timer(user_id)

    if not timer:
        await message.answer(
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

    await message.answer(
        f"⏱ **Текущая фокус-сессия:**\n\n"
        f"🎯 Привычка: «{timer['habit_name']}»\n"
        f"⏳ Прошло: **{spent_min} мин** | Осталось: **{remaining_min} мин**\n"
        f"📊 Статус: **{status}**",
        reply_markup=get_timer_controls_keyboard(is_paused=is_paused),
        parse_mode="Markdown",
    )
