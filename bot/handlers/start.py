"""Handler for onboarding, registration, timezone, and check-in configuration."""

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.database import habits_repo, users_repo
from bot.keyboards.inline import (
    get_checkin_time_inline_keyboard,
    get_mute_duration_keyboard,
    get_notifications_hub_keyboard,
    get_settings_hub_keyboard,
    get_timezone_inline_keyboard,
)
from bot.keyboards.reply import get_main_reply_keyboard
from bot.states.habit_states import OnboardingStates

router = Router(name="start_router")


def calculate_offset_from_time_str(user_time_str: str) -> Optional[int]:
    """Calculate UTC offset (-12 to +14) based on user's current HH:MM string."""
    match = re.match(r"^([0-1]?\d|2[0-3]):([0-5]\d)$", user_time_str.strip())
    if not match:
        return None

    user_h, user_m = int(match.group(1)), int(match.group(2))
    utc_now = datetime.now(timezone.utc)
    utc_minutes = utc_now.hour * 60 + utc_now.minute
    user_minutes = user_h * 60 + user_m

    diff = user_minutes - utc_minutes
    # Normalize to -12h ... +14h
    if diff < -720:
        diff += 1440
    elif diff > 840:
        diff -= 1440

    offset = round(diff / 60)
    return max(-12, min(14, offset))


@router.message(Command("cancel"))
@router.message(F.text.casefold().in_(["/отмена", "отмена", "cancel", "/cancel"]))
async def global_cmd_cancel(message: Message, state: FSMContext) -> None:
    """Universally cancel any active FSM state across the bot."""
    current_state = await state.get_state()
    await state.clear()
    if current_state:
        await message.answer(
            "❌ Действие отменено.",
            reply_markup=get_main_reply_keyboard(),
        )
    else:
        await message.answer(
            "ℹ️ Нет активного действия для отмены.",
            reply_markup=get_main_reply_keyboard(),
        )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    user_data: Optional[Dict[str, Any]] = None,
) -> None:
    """Entry point for /start command."""
    if user_data:
        offset = user_data.get("utc_offset", 3)
        ci_time = user_data.get("checkin_time", "21:00")
        name = message.from_user.first_name if message.from_user else "друг"
        await message.answer(
            f"👋 С возвращением, {name}!\n\n"
            f"⚙️ Ваш часовой пояс: **UTC{offset:+d}**\n"
            f"🌙 Время вечернего чек-ина: **{ci_time}**\n\n"
            "Используйте кнопки меню ниже для управления привычками и фокус-сессиями.",
            reply_markup=get_main_reply_keyboard(),
            parse_mode="Markdown",
        )
        return

    await state.set_state(OnboardingStates.waiting_for_timezone)
    await message.answer(
        "👋 Привет! Я помогу тебе внедрять полезные привычки, сохранять глубокий фокус и не терять стрики.\n\n"
        "Для корректной работы таймеров и напоминаний:\n"
        "**Укажите ваше текущее местное время** (например, `19:40`) или выберите часовой пояс кнопкой ниже:",
        reply_markup=get_timezone_inline_keyboard(),
        parse_mode="Markdown",
    )


@router.callback_query(OnboardingStates.waiting_for_timezone, F.data.startswith("set_tz:"))
async def cb_select_tz(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle timezone preset button press."""
    offset = int(callback.data.split(":")[1])
    await state.update_data(utc_offset=offset)
    await state.set_state(OnboardingStates.waiting_for_checkin_time)

    await callback.message.edit_text(
        f"✅ Часовой пояс сохранен: **UTC{offset:+d}**.\n\n"
        "Во сколько присылать вечерний отчет/чек-ин?\n"
        "Выберите удобное время или напишите его в формате `HH:MM` (например, `21:30`):",
        reply_markup=get_checkin_time_inline_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(OnboardingStates.waiting_for_timezone)
async def msg_timezone_input(message: Message, state: FSMContext) -> None:
    """Handle freeform HH:MM text for timezone calculation."""
    text = (message.text or "").strip()
    offset = calculate_offset_from_time_str(text)
    if offset is None:
        await message.answer(
            "⚠️ Не удалось распознать время. Пожалуйста, напишите время в формате `ЧЧ:ММ` (например, `19:40`) "
            "или выберите вариант из кнопок выше.",
            parse_mode="Markdown",
        )
        return

    await state.update_data(utc_offset=offset)
    await state.set_state(OnboardingStates.waiting_for_checkin_time)

    await message.answer(
        f"✅ Часовой пояс определен: **UTC{offset:+d}**.\n\n"
        "Во сколько присылать вечерний отчет/чек-ин?\n"
        "Выберите подходящий вариант или отправьте время в формате `HH:MM` (по умолчанию 21:00):",
        reply_markup=get_checkin_time_inline_keyboard(),
        parse_mode="Markdown",
    )


@router.callback_query(OnboardingStates.waiting_for_checkin_time, F.data.startswith("set_ci:"))
async def cb_select_checkin(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle check-in time preset button press."""
    ci_time = callback.data.split(":", 1)[1]
    data = await state.get_data()
    offset = data.get("utc_offset", 3)

    user_id = callback.from_user.id
    username = callback.from_user.username

    await users_repo.create_or_update_user(
        user_id=user_id,
        username=username,
        utc_offset=offset,
        checkin_time=ci_time,
    )
    await state.clear()

    await callback.message.edit_text(
        f"🎉 **Настройка завершена!**\n\n"
        f"🌍 Часовой пояс: **UTC{offset:+d}**\n"
        f"🌙 Вечерний чек-ин: **{ci_time}**\n\n"
        "Теперь вы можете добавить свою первую привычку через меню или команду `/new`!",
        parse_mode="Markdown",
    )
    await callback.message.answer(
        "Главное меню активировано 👇",
        reply_markup=get_main_reply_keyboard(),
    )
    await callback.answer()


@router.message(OnboardingStates.waiting_for_checkin_time)
async def msg_checkin_input(message: Message, state: FSMContext) -> None:
    """Handle custom check-in time text."""
    text = (message.text or "").strip()
    match = re.match(r"^([0-1]?\d|2[0-3]):([0-5]\d)$", text)
    if not match:
        await message.answer(
            "⚠️ Пожалуйста, введите время в формате `ЧЧ:ММ` (например, `21:15`) или воспользуйтесь кнопками выше.",
            parse_mode="Markdown",
        )
        return

    ci_time = f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"
    data = await state.get_data()
    offset = data.get("utc_offset", 3)

    user_id = message.from_user.id
    username = message.from_user.username

    await users_repo.create_or_update_user(
        user_id=user_id,
        username=username,
        utc_offset=offset,
        checkin_time=ci_time,
    )
    await state.clear()

    await message.answer(
        f"🎉 **Настройка завершена!**\n\n"
        f"🌍 Часовой пояс: **UTC{offset:+d}**\n"
        f"🌙 Вечерний чек-ин: **{ci_time}**\n\n"
        "Теперь вы можете добавить привычку через меню или команду `/new`!",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown",
    )


def build_settings_view(user: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    """Assemble text and keyboard for settings dashboard."""
    offset = user.get("utc_offset", 3)
    ci_time = user.get("checkin_time", "21:00")
    freezes = user.get("freezes_left", 1)

    weight_enabled = bool(user.get("weight_reminder_enabled", 0))
    weight_time = user.get("weight_reminder_time", "08:00")
    weight_status = f"{weight_time} (Вкл)" if weight_enabled else "Выключено"

    now_ts = int(time.time())
    muted_until = user.get("notifications_muted_until", 0) or 0
    if muted_until > now_ts:
        if muted_until >= 2000000000:
            notif_status = "🔕 Отключены НАВСЕГДА"
        else:
            until_dt = datetime.fromtimestamp(muted_until, timezone.utc) + timedelta(hours=offset)
            notif_status = f"🔕 Заглушены до {until_dt.strftime('%d.%m %H:%M')}"
    else:
        notif_status = "🔔 Активны"

    text = (
        f"⚙️ **Настройки бота**\n\n"
        f"🌍 Часовой пояс: **UTC{offset:+d}**\n"
        f"🌙 Вечерний чек-ин: **{ci_time}**\n"
        f"⚖️ Утреннее взвешивание: **{weight_status}**\n"
        f"❄️ Заморозок стрика на неделю: **{freezes}**\n"
        f"🔔 Уведомления: **{notif_status}**\n\n"
        f"Выберите пункт для настройки:"
    )
    return text, get_settings_hub_keyboard()


def build_notifications_view(
    user: Dict[str, Any],
    habits: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Assemble text and keyboard for all notifications list."""
    offset = user.get("utc_offset", 3)
    ci_time = user.get("checkin_time", "21:00")
    weight_enabled = bool(user.get("weight_reminder_enabled", 0))
    weight_time = user.get("weight_reminder_time", "08:00")

    w_str = f"Включено (каждое утро в `{weight_time}`)" if weight_enabled else "Выключено"

    now_ts = int(time.time())
    muted_until = user.get("notifications_muted_until", 0) or 0
    is_muted = muted_until > now_ts

    if is_muted:
        if muted_until >= 2000000000:
            status_line = "🔕 **Все уведомления отключены НАВСЕГДА.**"
        else:
            until_dt = datetime.fromtimestamp(muted_until, timezone.utc) + timedelta(hours=offset)
            status_line = f"🔕 **Все уведомления временно отключены** до `{until_dt.strftime('%d.%m в %H:%M')}`."
    else:
        status_line = "🔔 **Все запланированные уведомления активны.**"

    habit_reminders = []
    if habits:
        for h in habits:
            if h.get("habit_type") == "checklist" and h.get("reminder_times"):
                reps = h.get("target_repeats", 1)
                habit_reminders.append(f"  • «{h['name']}» ({reps}x/день): `{h['reminder_times']}`")

    habits_str = ""
    if habit_reminders:
        habits_str = f"4. 💊 **Напоминания по привычкам**:\n" + "\n".join(habit_reminders) + "\n"

    text = (
        "🔔 **Управление всеми уведомлениями**\n\n"
        "Здесь отображаются все регулярные напоминания и их расписание:\n\n"
        f"1. 🌅 **Утреннее взвешивание**: {w_str}\n"
        f"2. 🌙 **Вечерний чек-ин**: Включено (каждый вечер в `{ci_time}`)\n"
        f"3. ⏱ **Таймеры фокуса**: Уведомление со звуком при завершении сессии\n"
        f"{habits_str}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_line}"
    )
    return text, get_notifications_hub_keyboard(is_muted=is_muted)


@router.message(F.text == "⚙️ Настройки")
@router.message(Command("settings"))
async def cmd_settings(message: Message, user_data: Optional[Dict[str, Any]] = None) -> None:
    """Show interactive settings dashboard."""
    if not user_data:
        user_data = await users_repo.get_user(message.from_user.id)

    text, kb = build_settings_view(user_data or {})
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "settings_main")
async def cb_settings_main(callback: CallbackQuery) -> None:
    """Return to main settings view."""
    user = await users_repo.get_user(callback.from_user.id)
    text, kb = build_settings_view(user or {})
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "settings_notifications")
async def cb_settings_notifications(callback: CallbackQuery) -> None:
    """Display all notifications dashboard."""
    user = await users_repo.get_user(callback.from_user.id)
    habits = await habits_repo.get_habits_for_user(callback.from_user.id)
    text, kb = build_notifications_view(user or {}, habits=habits)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "notif_mute_menu")
async def cb_notif_mute_menu(callback: CallbackQuery) -> None:
    """Show options for how long to mute notifications (disables button and shows durations)."""
    text = (
        "🔕 **Отключение всех уведомлений**\n\n"
        "Выберите срок, на который вы хотите заглушить все напоминания:\n\n"
        "В этот период бот не будет беспокоить вас утренними напоминаниями о взвешивании и вечерними чек-инами."
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_mute_duration_keyboard(), parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("notif_mute:"))
async def cb_notif_mute_duration(callback: CallbackQuery) -> None:
    """Save mute duration."""
    user_id = callback.from_user.id
    arg = callback.data.split(":")[1]
    now_ts = int(time.time())

    if arg == "forever":
        until_ts = 2147483647
        alert_msg = "Уведомления отключены навсегда 🔕"
    else:
        seconds = int(arg)
        until_ts = now_ts + seconds
        alert_msg = "Уведомления временно отключены 🔕"

    await users_repo.mute_notifications(user_id, until_ts)
    await callback.answer(alert_msg, show_alert=True)

    user = await users_repo.get_user(user_id)
    habits = await habits_repo.get_habits_for_user(user_id)
    text, kb = build_notifications_view(user or {}, habits=habits)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "notif_unmute")
async def cb_notif_unmute(callback: CallbackQuery) -> None:
    """Unmute all notifications."""
    user_id = callback.from_user.id
    await users_repo.unmute_notifications(user_id)
    await callback.answer("Уведомления снова включены! 🔔", show_alert=True)

    user = await users_repo.get_user(user_id)
    habits = await habits_repo.get_habits_for_user(user_id)
    text, kb = build_notifications_view(user or {}, habits=habits)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass


@router.callback_query(F.data == "settings_timezone")
async def cb_settings_timezone(callback: CallbackQuery, state: FSMContext) -> None:
    """Change timezone via button."""
    await state.set_state(OnboardingStates.waiting_for_timezone)
    await callback.message.answer(
        "Укажите ваше текущее время (например, `19:40`) или выберите часовой пояс ниже:",
        reply_markup=get_timezone_inline_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_ci_time")
async def cb_settings_ci_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Change checkin time via button."""
    await state.set_state(OnboardingStates.waiting_for_checkin_time)
    await callback.message.answer(
        "Выберите новое время вечернего чек-ина или напишите `HH:MM`:",
        reply_markup=get_checkin_time_inline_keyboard(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_export")
async def cb_settings_export(callback: CallbackQuery) -> None:
    """Trigger markdown export from settings."""
    from bot.handlers.stats import cmd_export
    user_id = callback.from_user.id
    user = await users_repo.get_user(user_id)
    offset = user.get("utc_offset", 3) if user else 3
    local_now = datetime.now(timezone.utc) + timedelta(hours=offset)
    local_date = local_now.strftime("%Y-%m-%d")
    await cmd_export(callback.message, local_date=local_date)
    await callback.answer()


@router.message(Command("timezone"))
async def cmd_change_timezone(message: Message, state: FSMContext) -> None:
    """Change timezone manually."""
    await state.set_state(OnboardingStates.waiting_for_timezone)
    await message.answer(
        "Укажите ваше текущее время (например, `19:40`) или выберите часовой пояс ниже:",
        reply_markup=get_timezone_inline_keyboard(),
        parse_mode="Markdown",
    )


@router.message(Command("checkin_time"))
async def cmd_change_checkin_time(message: Message, state: FSMContext) -> None:
    """Change check-in time manually."""
    await state.set_state(OnboardingStates.waiting_for_checkin_time)
    await message.answer(
        "Выберите новое время вечернего чек-ина или напишите `HH:MM`:",
        reply_markup=get_checkin_time_inline_keyboard(),
        parse_mode="Markdown",
    )
