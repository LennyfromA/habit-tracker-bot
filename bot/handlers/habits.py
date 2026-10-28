"""Handler for managing habits: list, interactive & quick creation, deletion, streak freezes."""

import re
from typing import Any, Dict, List, Optional, Set

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database import habits_repo, users_repo
from bot.keyboards.inline import (
    DAY_LABELS,
    get_days_selector_keyboard,
    get_edit_days_selector_keyboard,
    get_habit_actions_keyboard,
    get_habit_edit_menu_keyboard,
    get_habit_time_presets_keyboard,
    get_habits_hub_keyboard,
)
from bot.keyboards.reply import get_main_reply_keyboard
from bot.states.habit_states import HabitCreationStates, HabitEditStates

router = Router(name="habits_router")


def render_progress_bar(spent: int, target: int, length: int = 8) -> str:
    """Format visual progress bar like [■■■■□□□□]."""
    if target <= 0:
        return "[" + "■" * length + "]"
    ratio = min(1.0, max(0.0, spent / target))
    filled = round(ratio * length)
    empty = length - filled
    return f"[{'■' * filled}{'□' * empty}]"


def parse_days_argument(days_arg: str) -> str:
    """Parse days shortcut into comma-separated numbers (1=Mon ... 7=Sun)."""
    arg = days_arg.lower().strip()
    if arg in ("weekdays", "будни"):
        return "1,2,3,4,5"
    if arg in ("weekends", "выходные"):
        return "6,7"
    if arg in ("daily", "all", "everyday", "каждый", "каждый день"):
        return "1,2,3,4,5,6,7"

    # Match numbers separated by comma: e.g. "1,3,5"
    valid_days = []
    for part in re.split(r"[,; ]+", arg):
        if part.isdigit() and 1 <= int(part) <= 7:
            valid_days.append(str(int(part)))
    if valid_days:
        return ",".join(sorted(set(valid_days), key=int))

    return "1,2,3,4,5,6,7"


# ---------------------------------------------------------
# Habit Listing
# ---------------------------------------------------------

@router.message(F.text.in_(["📋 Привычки", "📋 Мои привычки"]))
@router.message(Command("habits"))
async def cmd_habits_list(
    message: Message,
    user_data: Optional[Dict[str, Any]],
    local_date: str,
    local_weekday: int,
) -> None:
    """Display active habits for today with visual progress bars and action buttons."""
    user_id = message.from_user.id
    habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)

    user = user_data or await users_repo.get_user(user_id)
    freezes_left = user.get("freezes_left", 0) if user else 0

    if not habits:
        all_habits = await habits_repo.get_habits_for_user(user_id)
        if not all_habits:
            await message.answer(
                "📋 У вас пока нет созданных привычек.\n\n"
                "Нажмите «➕ Новая привычка» ниже или отправьте `/new <Название> <Минуты>`, чтобы добавить первую!",
                reply_markup=get_habits_hub_keyboard(),
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                f"🌴 На сегодня ({DAY_LABELS.get(local_weekday, '')}, {local_date}) запланированных привычек нет!\n\n"
                f"Всего у вас создано привычек: {len(all_habits)}. Чтобы посмотреть все: `/habits_all`.",
                reply_markup=get_habits_hub_keyboard(),
                parse_mode="Markdown",
            )
        return

    await message.answer(
        f"📋 **Ваши привычки на сегодня ({DAY_LABELS.get(local_weekday, '')}, {local_date}):**\n"
        f"❄️ Доступно заморозок стрика на этой неделе: **{freezes_left}**",
        parse_mode="Markdown",
    )

    for h in habits:
        habit_id = h["id"]
        name = h["name"]
        target = h["target_minutes"]
        spent = h.get("spent_minutes", 0)
        streak = h.get("streak_days", 0)
        is_completed = bool(h.get("is_completed"))
        is_frozen = bool(h.get("is_frozen_today"))
        remaining = max(0, target - spent)

        bar = render_progress_bar(spent, target)

        if is_frozen:
            status_text = f"❄️ Заморожено на сегодня | 🔥 Стрик: {streak} дн."
        elif is_completed:
            status_text = f"{bar} {spent}/{target} мин (Выполнено! 🎉) | 🔥 Стрик: {streak} дн."
        else:
            status_text = f"{bar} {spent}/{target} мин (Осталось {remaining} мин) | 🔥 Стрик: {streak} дн."

        card_text = f"🎯 **{name}**\n{status_text}"
        kb = get_habit_actions_keyboard(
            habit_id=habit_id,
            remaining_minutes=remaining,
            is_completed=is_completed,
            is_frozen_today=is_frozen,
            can_freeze=(freezes_left > 0),
        )
        await message.answer(card_text, reply_markup=kb, parse_mode="Markdown")

    await message.answer(
        "⚡ **Действия с привычками и фокусом:**",
        reply_markup=get_habits_hub_keyboard(),
        parse_mode="Markdown",
    )


@router.message(Command("habits_all"))
async def cmd_habits_all(message: Message, local_date: str) -> None:
    """Show all habits regardless of scheduled day."""
    user_id = message.from_user.id
    habits = await habits_repo.get_all_habits_with_today_status(user_id, local_date)
    if not habits:
        await message.answer("У вас пока нет созданных привычек. Создайте через команду `/new`.", parse_mode="Markdown")
        return

    text_lines = ["📋 **Все ваши привычки:**\n"]
    for h in habits:
        days = ",".join(DAY_LABELS.get(int(d), d) for d in h["active_days"].split(","))
        text_lines.append(
            f"• **{h['name']}** — цель {h['target_minutes']} мин/день\n"
            f"  Дни: {days} | 🔥 Стрик: {h['streak_days']} дн."
        )

    await message.answer("\n\n".join(text_lines), parse_mode="Markdown")


# ---------------------------------------------------------
# Habit Creation (FSM & Quick Syntax)
# ---------------------------------------------------------

@router.message(Command("new"))
async def cmd_new_habit(message: Message, state: FSMContext) -> None:
    """
    Handle /new command. Supports quick syntax:
    /new Английский 45 weekdays
    /new Бег 30 1,3,5
    /new Чтение 20
    """
    command_text = (message.text or "").strip()
    parts = command_text.split()

    if len(parts) >= 3:
        # Quick creation attempt
        # Format: /new <name...> <minutes> [days]
        if parts[-1].isdigit():
            # No days arg provided, target_minutes is last
            target_min = int(parts[-1])
            name = " ".join(parts[1:-1])
            active_days = "1,2,3,4,5,6,7"
        elif len(parts) >= 4 and parts[-2].isdigit():
            # Days arg provided
            target_min = int(parts[-2])
            active_days = parse_days_argument(parts[-1])
            name = " ".join(parts[1:-2])
        else:
            target_min = None

        if target_min and target_min > 0 and name:
            habit_id = await habits_repo.create_habit(
                user_id=message.from_user.id,
                name=name,
                target_minutes=target_min,
                active_days=active_days,
            )
            days_str = ",".join(DAY_LABELS.get(int(d), d) for d in active_days.split(","))
            await message.answer(
                f"✅ **Привычка успешно создана!**\n\n"
                f"🎯 Название: **{name}**\n"
                f"⏱ Цель: **{target_min} мин** в день\n"
                f"📅 Активные дни: **{days_str}**",
                reply_markup=get_main_reply_keyboard(),
                parse_mode="Markdown",
            )
            return

    # Fallback to interactive FSM
    await state.set_state(HabitCreationStates.waiting_for_name)
    await message.answer(
        "📝 **Создание новой привычки**\n\n"
        "Шаг 1 из 3: Введите название привычки (например: `Чтение`, `Английский`, `Спорт`):",
        parse_mode="Markdown",
    )


@router.message(F.text == "➕ Новая привычка")
async def btn_new_habit(message: Message, state: FSMContext) -> None:
    """Interactive FSM creation triggered from reply menu."""
    await state.set_state(HabitCreationStates.waiting_for_name)
    await message.answer(
        "📝 **Создание новой привычки**\n\n"
        "Шаг 1 из 3: Введите название привычки (например: `Чтение`, `Английский`, `Спорт`):",
        parse_mode="Markdown",
    )


@router.message(HabitCreationStates.waiting_for_name)
async def fsm_habit_name(message: Message, state: FSMContext) -> None:
    """Save name and ask for daily target minutes."""
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 60:
        await message.answer("⚠️ Название должно быть от 2 до 60 символов. Попробуйте еще раз:")
        return

    await state.update_data(habit_name=name)
    await state.set_state(HabitCreationStates.waiting_for_target_minutes)
    await message.answer(
        f"Отлично, «{name}»!\n\n"
        "Шаг 2 из 3: Укажите ежедневную цель в минутах (например: `25`, `45`, `60`):",
        parse_mode="Markdown",
    )


@router.message(HabitCreationStates.waiting_for_target_minutes)
async def fsm_habit_target(message: Message, state: FSMContext) -> None:
    """Save target minutes and display day-of-week selection keyboard."""
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0 or int(text) > 720:
        await message.answer("⚠️ Пожалуйста, введите положительное число минут от 1 до 720 (12 часов):")
        return

    target_min = int(text)
    # Default to all days selected: {1, 2, 3, 4, 5, 6, 7}
    selected_days: Set[int] = {1, 2, 3, 4, 5, 6, 7}
    await state.update_data(target_minutes=target_min, selected_days=list(selected_days))
    await state.set_state(HabitCreationStates.waiting_for_active_days)

    await message.answer(
        f"🎯 Цель: **{target_min} минут** в день.\n\n"
        "Шаг 3 из 3: Выберите дни недели, в которые привычка должна быть активна:",
        reply_markup=get_days_selector_keyboard(selected_days),
        parse_mode="Markdown",
    )


@router.callback_query(HabitCreationStates.waiting_for_active_days, F.data.startswith("toggle_day:"))
async def cb_toggle_day(callback: CallbackQuery, state: FSMContext) -> None:
    """Toggle individual day on/off."""
    day = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected_days: Set[int] = set(data.get("selected_days", []))

    if day in selected_days:
        if len(selected_days) > 1:
            selected_days.remove(day)
        else:
            await callback.answer("Привычка должна быть активна хотя бы в один день!", show_alert=True)
            return
    else:
        selected_days.add(day)

    await state.update_data(selected_days=list(selected_days))
    await callback.message.edit_reply_markup(reply_markup=get_days_selector_keyboard(selected_days))
    await callback.answer()


@router.callback_query(HabitCreationStates.waiting_for_active_days, F.data.startswith("preset_days:"))
async def cb_preset_days(callback: CallbackQuery, state: FSMContext) -> None:
    """Apply preset (all, weekdays, weekends)."""
    preset = callback.data.split(":")[1]
    if preset == "all":
        selected_days = {1, 2, 3, 4, 5, 6, 7}
    elif preset == "weekdays":
        selected_days = {1, 2, 3, 4, 5}
    elif preset == "weekends":
        selected_days = {6, 7}
    else:
        selected_days = {1, 2, 3, 4, 5, 6, 7}

    await state.update_data(selected_days=list(selected_days))
    await callback.message.edit_reply_markup(reply_markup=get_days_selector_keyboard(selected_days))
    await callback.answer()


@router.callback_query(HabitCreationStates.waiting_for_active_days, F.data == "finish_days_selection")
async def cb_finish_habit_creation(callback: CallbackQuery, state: FSMContext) -> None:
    """Persist completed habit to database."""
    data = await state.get_data()
    name = data.get("habit_name", "Новая привычка")
    target_min = data.get("target_minutes", 30)
    selected_days = sorted(data.get("selected_days", [1, 2, 3, 4, 5, 6, 7]))
    active_days_str = ",".join(str(d) for d in selected_days)

    await habits_repo.create_habit(
        user_id=callback.from_user.id,
        name=name,
        target_minutes=target_min,
        active_days=active_days_str,
    )
    await state.clear()

    days_str = ", ".join(DAY_LABELS.get(d, str(d)) for d in selected_days)
    await callback.message.edit_text(
        f"🎉 **Привычка успешно создана!**\n\n"
        f"🎯 Название: **{name}**\n"
        f"⏱ Цель: **{target_min} мин** в день\n"
        f"📅 Активные дни: **{days_str}**",
        parse_mode="Markdown",
    )
    await callback.message.answer(
        "Вы можете сразу запустить фокус-сессию в меню «📋 Мои привычки»!",
        reply_markup=get_main_reply_keyboard(),
    )
    await callback.answer()


# ---------------------------------------------------------
# Streak Freeze & Delete Handlers
# ---------------------------------------------------------

@router.callback_query(F.data.startswith("freeze_habit:"))
async def cb_freeze_habit(callback: CallbackQuery) -> None:
    """Freeze streak for today for selected habit."""
    habit_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    success, msg = await habits_repo.freeze_habit_today(habit_id, user_id)
    await callback.answer(msg, show_alert=True)
    if success:
        # Update button to frozen state
        try:
            await callback.message.edit_text(
                f"{callback.message.text}\n\n*(Заморозка активирована!)*",
                reply_markup=None,
            )
        except Exception:
            pass


@router.callback_query(F.data == "already_frozen")
async def cb_already_frozen(callback: CallbackQuery) -> None:
    """Inform user that habit is already frozen."""
    await callback.answer("Эта привычка уже защищена заморозкой на сегодня!", show_alert=True)


@router.callback_query(F.data.startswith("delete_habit:"))
async def cb_delete_habit(callback: CallbackQuery) -> None:
    """Delete a habit."""
    habit_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        await callback.answer("Привычка не найдена.", show_alert=True)
        return

    await habits_repo.delete_habit(habit_id, user_id)
    await callback.answer("Привычка удалена.", show_alert=True)
    try:
        await callback.message.edit_text(
            f"❌ Привычка «{habit['name']}» была удалена.",
            reply_markup=None,
        )
    except Exception:
        pass


# ---------------------------------------------------------
# Habits Hub Action Callbacks
# ---------------------------------------------------------

@router.callback_query(F.data == "habits_hub:new")
async def cb_habits_hub_new(callback: CallbackQuery, state: FSMContext) -> None:
    """Launch interactive habit creation wizard."""
    await state.set_state(HabitCreationStates.waiting_for_name)
    await callback.message.answer(
        "📝 **Шаг 1 из 3:** Введите название новой привычки (например: «Чтение книг» или «Медитация»):\n"
        "_(Отправьте /cancel для отмены)_",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "habits_hub:timer")
async def cb_habits_hub_timer(callback: CallbackQuery) -> None:
    """Redirect to focus timer."""
    from bot.handlers.timer import cmd_active_timer
    await cmd_active_timer(callback.message)
    await callback.answer()


@router.callback_query(F.data == "habits_hub:checkin")
async def cb_habits_hub_checkin(
    callback: CallbackQuery,
    local_date: str,
    local_weekday: int,
) -> None:
    """Redirect to evening checkin."""
    from bot.handlers.checkin import cmd_checkin
    await cmd_checkin(callback.message, local_date=local_date, local_weekday=local_weekday)
    await callback.answer()


@router.callback_query(F.data == "habits_hub:stats")
async def cb_habits_hub_stats(callback: CallbackQuery, local_date: str) -> None:
    """Redirect to statistics and charts."""
    from bot.handlers.stats import cmd_stats
    await cmd_stats(callback.message, local_date=local_date)
    await callback.answer()


# ---------------------------------------------------------
# Habit Editing Handlers
# ---------------------------------------------------------

def format_habit_edit_card(habit: Dict[str, Any]) -> str:
    """Format markdown summary of habit settings for the edit dashboard."""
    name = habit["name"]
    target = habit["target_minutes"]
    active_days_raw = str(habit.get("active_days") or "1,2,3,4,5,6,7")
    days_labels = ", ".join(DAY_LABELS.get(int(d), str(d)) for d in active_days_raw.split(",") if d.isdigit())
    if active_days_raw == "1,2,3,4,5,6,7":
        days_str = "Каждый день (Пн–Вс)"
    elif active_days_raw == "1,2,3,4,5":
        days_str = "Будни (Пн–Пт)"
    elif active_days_raw == "6,7":
        days_str = "Выходные (Сб–Вс)"
    else:
        days_str = days_labels

    return (
        f"✏️ **Редактирование привычки «{name}»**\n\n"
        f"🎯 **Название:** {name}\n"
        f"⏱ **Цель:** {target} мин/день\n"
        f"📅 **Дни активности / чек-ина:** {days_str}\n\n"
        f"Выберите, что вы хотите изменить:"
    )


@router.callback_query(F.data.startswith("edit_habit_menu:"))
async def cb_edit_habit_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Display edit menu for selected habit."""
    await state.clear()
    habit_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        await callback.answer("Привычка не найдена.", show_alert=True)
        return

    text = format_habit_edit_card(habit)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_habit_edit_menu_keyboard(habit_id),
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("edit_h_name:"))
async def cb_edit_h_name(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user for new habit name."""
    habit_id = int(callback.data.split(":")[1])
    await state.set_state(HabitEditStates.waiting_for_name)
    await state.update_data(editing_habit_id=habit_id)
    await callback.message.edit_text(
        "📝 **Введите новое название для этой привычки:**\n\n"
        "_(Отправьте /cancel для отмены)_",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(HabitEditStates.waiting_for_name)
async def process_edit_habit_name(message: Message, state: FSMContext) -> None:
    """Process updated habit name."""
    new_name = (message.text or "").strip()
    if not new_name or len(new_name) > 64:
        await message.answer("⚠️ Название должно быть от 1 до 64 символов. Попробуйте еще раз или /cancel.")
        return

    data = await state.get_data()
    habit_id = data.get("editing_habit_id")
    await state.clear()

    user_id = message.from_user.id
    updated = await habits_repo.update_habit(habit_id, user_id, name=new_name)
    if not updated:
        await message.answer("⚠️ Привычка не найдена.", reply_markup=get_main_reply_keyboard())
        return

    text = format_habit_edit_card(updated)
    await message.answer(
        f"✅ Название успешно изменено на «**{new_name}**»!\n\n" + text,
        reply_markup=get_habit_edit_menu_keyboard(habit_id),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("edit_h_time:"))
async def cb_edit_h_time(callback: CallbackQuery) -> None:
    """Display duration presets for habit target."""
    habit_id = int(callback.data.split(":")[1])
    habit = await habits_repo.get_habit_by_id(habit_id)
    cur_mins = habit["target_minutes"] if habit else 30

    await callback.message.edit_text(
        f"⏱ **Изменение ежедневной цели по времени**\n\n"
        f"Текущая цель: **{cur_mins} мин/день**.\n\n"
        f"Выберите готовое значение или введите своё число минут:",
        reply_markup=get_habit_time_presets_keyboard(habit_id),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_h_time:"))
async def cb_set_h_time(callback: CallbackQuery) -> None:
    """Set habit duration from preset buttons."""
    parts = callback.data.split(":")
    habit_id = int(parts[1])
    mins = int(parts[2])
    user_id = callback.from_user.id

    updated = await habits_repo.update_habit(habit_id, user_id, target_minutes=mins)
    await callback.answer(f"Цель обновлена: {mins} мин! ✅", show_alert=True)

    if updated:
        text = format_habit_edit_card(updated)
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_habit_edit_menu_keyboard(habit_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("custom_h_time:"))
async def cb_custom_h_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type custom minutes."""
    habit_id = int(callback.data.split(":")[1])
    await state.set_state(HabitEditStates.waiting_for_target_minutes)
    await state.update_data(editing_habit_id=habit_id)
    await callback.message.edit_text(
        "✍️ **Введите желаемое целевое время в минутах (от 1 до 720):**\n\n"
        "_(Отправьте /cancel для отмены)_",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(HabitEditStates.waiting_for_target_minutes)
async def process_edit_habit_time(message: Message, state: FSMContext) -> None:
    """Process custom target minutes input."""
    text = (message.text or "").strip()
    match = re.search(r"\d+", text)
    if not match:
        await message.answer("⚠️ Введите число минут (например: `45`) или /cancel.")
        return

    mins = int(match.group(0))
    if mins < 1 or mins > 720:
        await message.answer("⚠️ Время должно быть от 1 до 720 минут (до 12 часов).")
        return

    data = await state.get_data()
    habit_id = data.get("editing_habit_id")
    await state.clear()

    user_id = message.from_user.id
    updated = await habits_repo.update_habit(habit_id, user_id, target_minutes=mins)
    if not updated:
        await message.answer("⚠️ Привычка не найдена.", reply_markup=get_main_reply_keyboard())
        return

    card_text = format_habit_edit_card(updated)
    await message.answer(
        f"✅ Цель по времени успешно изменена на **{mins} мин/день**!\n\n" + card_text,
        reply_markup=get_habit_edit_menu_keyboard(habit_id),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("edit_h_days:"))
async def cb_edit_h_days(callback: CallbackQuery, state: FSMContext) -> None:
    """Open day selector keyboard for habit editing."""
    habit_id = int(callback.data.split(":")[1])
    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit:
        await callback.answer("Привычка не найдена.")
        return

    days_str = habit.get("active_days", "1,2,3,4,5,6,7")
    selected_days = set(int(d) for d in days_str.split(",") if d.isdigit())

    await state.set_state(HabitEditStates.waiting_for_active_days)
    await state.update_data(editing_habit_id=habit_id, edit_days=selected_days)

    await callback.message.edit_text(
        "📅 **Выберите дни, в которые привычка должна быть активна:**\n\n"
        "В выбранные дни привычка будет отображаться в плане и проверяться в вечернем чек-ине.",
        reply_markup=get_edit_days_selector_keyboard(habit_id, selected_days),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_edit_day:"))
async def cb_toggle_edit_day(callback: CallbackQuery, state: FSMContext) -> None:
    """Toggle a day of the week in edit mode."""
    parts = callback.data.split(":")
    habit_id = int(parts[1])
    day = int(parts[2])

    data = await state.get_data()
    days: Set[int] = data.get("edit_days", set())
    if day in days:
        days.remove(day)
    else:
        days.add(day)

    await state.update_data(edit_days=days)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_edit_days_selector_keyboard(habit_id, days)
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("preset_edit_days:"))
async def cb_preset_edit_days(callback: CallbackQuery, state: FSMContext) -> None:
    """Set day preset in edit mode."""
    parts = callback.data.split(":")
    habit_id = int(parts[1])
    preset = parts[2]

    if preset == "all":
        days = {1, 2, 3, 4, 5, 6, 7}
    elif preset == "weekdays":
        days = {1, 2, 3, 4, 5}
    elif preset == "weekends":
        days = {6, 7}
    else:
        days = {1, 2, 3, 4, 5, 6, 7}

    await state.update_data(edit_days=days)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_edit_days_selector_keyboard(habit_id, days)
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("save_edit_days:"))
async def cb_save_edit_days(callback: CallbackQuery, state: FSMContext) -> None:
    """Save updated active days to database."""
    habit_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    days: Set[int] = data.get("edit_days", set())

    if not days:
        await callback.answer("Выберите хотя бы один активный день!", show_alert=True)
        return

    days_str = ",".join(str(d) for d in sorted(days))
    user_id = callback.from_user.id
    await state.clear()

    updated = await habits_repo.update_habit(habit_id, user_id, active_days=days_str)
    await callback.answer("Дни активности успешно обновлены! ✅", show_alert=True)

    if updated:
        card_text = format_habit_edit_card(updated)
        try:
            await callback.message.edit_text(
                card_text,
                reply_markup=get_habit_edit_menu_keyboard(habit_id),
                parse_mode="Markdown",
            )
        except Exception:
            pass


@router.callback_query(F.data == "habits_refresh")
async def cb_habits_refresh(
    callback: CallbackQuery,
    user_data: Optional[Dict[str, Any]],
    local_date: str,
    local_weekday: int,
    state: FSMContext,
) -> None:
    """Refresh habits list from inline back button."""
    await state.clear()
    await cmd_habits_list(callback.message, user_data=user_data, local_date=local_date, local_weekday=local_weekday)
    await callback.answer("Обновлено")
