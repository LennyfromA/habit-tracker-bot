"""Handler for onboarding, registration, timezone, and check-in configuration."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database import users_repo
from bot.keyboards.inline import (
    get_checkin_time_inline_keyboard,
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


@router.message(F.text == "⚙️ Настройки")
@router.message(Command("settings"))
async def cmd_settings(message: Message, user_data: Optional[Dict[str, Any]] = None) -> None:
    """Show current settings and allow reconfiguration."""
    if not user_data:
        user_data = await users_repo.get_user(message.from_user.id)

    offset = user_data.get("utc_offset", 3) if user_data else 3
    ci_time = user_data.get("checkin_time", "21:00") if user_data else "21:00"
    freezes = user_data.get("freezes_left", 1) if user_data else 1

    await message.answer(
        f"⚙️ **Текущие настройки:**\n\n"
        f"🌍 Часовой пояс: **UTC{offset:+d}**\n"
        f"🌙 Время вечернего чек-ина: **{ci_time}**\n"
        f"❄️ Доступно заморозок на неделю: **{freezes}**\n\n"
        "Для смены часового пояса отправьте команду `/timezone`.\n"
        "Для изменения времени чек-ина отправьте `/checkin_time`.",
        parse_mode="Markdown",
    )


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
