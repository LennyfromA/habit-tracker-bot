"""Inline keyboards for interactive actions: timers, habits, check-in, timezone."""

from typing import Iterable, List, Set
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DAY_LABELS = {
    1: "Пн",
    2: "Вт",
    3: "Ср",
    4: "Чт",
    5: "Пт",
    6: "Сб",
    7: "Вс",
}


def get_timezone_inline_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with common UTC offsets."""
    keyboard = [
        [
            InlineKeyboardButton(text="UTC+2 (Калининград)", callback_data="set_tz:2"),
            InlineKeyboardButton(text="UTC+3 (Москва)", callback_data="set_tz:3"),
        ],
        [
            InlineKeyboardButton(text="UTC+5 (Екатеринбург)", callback_data="set_tz:5"),
            InlineKeyboardButton(text="UTC+7 (Новосибирск)", callback_data="set_tz:7"),
        ],
        [
            InlineKeyboardButton(text="UTC+0 (Лондон)", callback_data="set_tz:0"),
            InlineKeyboardButton(text="UTC+4 (Самара, Баку)", callback_data="set_tz:4"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_checkin_time_inline_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting standard evening check-in time."""
    keyboard = [
        [
            InlineKeyboardButton(text="20:00", callback_data="set_ci:20:00"),
            InlineKeyboardButton(text="21:00 (по умолчанию)", callback_data="set_ci:21:00"),
        ],
        [
            InlineKeyboardButton(text="21:30", callback_data="set_ci:21:30"),
            InlineKeyboardButton(text="22:00", callback_data="set_ci:22:00"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_days_selector_keyboard(selected_days: Set[int]) -> InlineKeyboardMarkup:
    """Build day-of-week toggle keyboard for habit creation."""
    row1 = [
        InlineKeyboardButton(
            text=f"{DAY_LABELS[d]} {'✅' if d in selected_days else '◻️'}",
            callback_data=f"toggle_day:{d}",
        )
        for d in [1, 2, 3, 4]
    ]

    row2 = [
        InlineKeyboardButton(
            text=f"{DAY_LABELS[d]} {'✅' if d in selected_days else '◻️'}",
            callback_data=f"toggle_day:{d}",
        )
        for d in [5, 6, 7]
    ]

    row3 = [
        InlineKeyboardButton(text="📅 Каждый день", callback_data="preset_days:all"),
        InlineKeyboardButton(text="💼 Будни", callback_data="preset_days:weekdays"),
        InlineKeyboardButton(text="🏖 Выходные", callback_data="preset_days:weekends"),
    ]

    row4 = [
        InlineKeyboardButton(text="Готово ✔️", callback_data="finish_days_selection"),
    ]

    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3, row4])


def get_habit_actions_keyboard(
    habit_id: int,
    remaining_minutes: int,
    is_completed: bool,
    is_frozen_today: bool,
    can_freeze: bool,
) -> InlineKeyboardMarkup:
    """Keyboard for a single habit in habit list."""
    buttons: List[List[InlineKeyboardButton]] = []

    if is_completed:
        timer_text = "🎉 Выполнено на сегодня! (Ещё фокус?)"
    elif remaining_minutes > 0:
        timer_text = f"▶ Запустить таймер (Остаток: {remaining_minutes} мин)"
    else:
        timer_text = "▶ Запустить таймер"

    buttons.append([InlineKeyboardButton(text=timer_text, callback_data=f"timer_start:{habit_id}")])

    second_row = []
    if is_frozen_today:
        second_row.append(
            InlineKeyboardButton(text="❄️ Заморожено сегодня", callback_data="already_frozen")
        )
    elif can_freeze:
        second_row.append(
            InlineKeyboardButton(text="❄️ Заморозить день", callback_data=f"freeze_habit:{habit_id}")
        )

    second_row.append(
        InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_habit:{habit_id}")
    )
    buttons.append(second_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_timer_controls_keyboard(is_paused: bool) -> InlineKeyboardMarkup:
    """Keyboard for controlling active focus session."""
    play_pause_btn = (
        InlineKeyboardButton(text="▶ Возобновить", callback_data="timer_action:resume")
        if is_paused
        else InlineKeyboardButton(text="⏸ Пауза", callback_data="timer_action:pause")
    )

    keyboard = [
        [play_pause_btn],
        [
            InlineKeyboardButton(text="➕ +10 мин", callback_data="timer_action:add_10"),
            InlineKeyboardButton(text="⏹ Завершить досрочно", callback_data="timer_action:stop"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_checkin_dashboard_keyboard(habits: List[dict]) -> InlineKeyboardMarkup:
    """Build interactive dashboard keyboard for evening check-in."""
    rows: List[List[InlineKeyboardButton]] = []

    # Filter incomplete habits
    for h in habits:
        if not h.get("is_completed") and not h.get("is_frozen_today"):
            remaining = max(0, h["target_minutes"] - h.get("spent_minutes", 0))
            btn_text = f"✅ Выполнить: {h['name']} ({remaining} мин)"
            rows.append(
                [InlineKeyboardButton(text=btn_text, callback_data=f"checkin_complete:{h['id']}")]
            )

    rows.append([InlineKeyboardButton(text="🎉 Завершить чек-ин", callback_data="checkin_finish")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_nutrition_dashboard_keyboard(has_meals: bool = False, has_water: bool = False) -> InlineKeyboardMarkup:
    """Build dashboard keyboard for nutrition & hydration menu."""
    keyboard = [
        [
            InlineKeyboardButton(text="💧 +250 мл", callback_data="water_add:250"),
            InlineKeyboardButton(text="🍶 +500 мл", callback_data="water_add:500"),
            InlineKeyboardButton(text="➕ Вода", callback_data="water_custom"),
        ],
        [
            InlineKeyboardButton(text="➕ Добавить еду", callback_data="nutr_add"),
            InlineKeyboardButton(text="⚖️ Мой вес", callback_data="weight_menu"),
        ],
        [
            InlineKeyboardButton(text="🎯 Моя норма", callback_data="nutr_set_goal"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="nutr_refresh"),
        ],
    ]
    actions_row = []
    if has_meals:
        actions_row.append(InlineKeyboardButton(text="🗑 Удалить еду", callback_data="nutr_delete_menu"))
    if has_water:
        actions_row.append(InlineKeyboardButton(text="↩️ Отменить воду", callback_data="water_undo"))

    if actions_row:
        keyboard.append(actions_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_weight_dashboard_keyboard(reminder_enabled: bool) -> InlineKeyboardMarkup:
    """Build keyboard for weight tracking menu."""
    rem_text = "🔔 Напоминание: ВКЛ" if reminder_enabled else "🔕 Напоминание: ВЫКЛ"
    keyboard = [
        [
            InlineKeyboardButton(text="⚖️ Записать вес", callback_data="weight_enter"),
        ],
        [
            InlineKeyboardButton(text=rem_text, callback_data="weight_toggle_reminder"),
            InlineKeyboardButton(text="⏰ Время", callback_data="weight_choose_time"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к питанию", callback_data="nutr_refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_weight_reminder_time_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard with preset times for morning weight reminder."""
    keyboard = [
        [
            InlineKeyboardButton(text="07:00", callback_data="weight_set_time:07:00"),
            InlineKeyboardButton(text="07:30", callback_data="weight_set_time:07:30"),
        ],
        [
            InlineKeyboardButton(text="08:00 (стандарт)", callback_data="weight_set_time:08:00"),
            InlineKeyboardButton(text="08:30", callback_data="weight_set_time:08:30"),
        ],
        [
            InlineKeyboardButton(text="09:00", callback_data="weight_set_time:09:00"),
            InlineKeyboardButton(text="10:00", callback_data="weight_set_time:10:00"),
        ],
        [
            InlineKeyboardButton(text="✍️ Ввести своё время", callback_data="weight_custom_time"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="weight_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_meal_category_inline_keyboard() -> InlineKeyboardMarkup:
    """Select meal category when adding food."""
    keyboard = [
        [
            InlineKeyboardButton(text="🍳 Завтрак", callback_data="nutr_cat:breakfast"),
            InlineKeyboardButton(text="🍲 Обед", callback_data="nutr_cat:lunch"),
        ],
        [
            InlineKeyboardButton(text="🍝 Ужин", callback_data="nutr_cat:dinner"),
            InlineKeyboardButton(text="🍏 Перекус", callback_data="nutr_cat:snack"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад в меню питания", callback_data="nutr_refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_meals_delete_keyboard(meals: List[dict]) -> InlineKeyboardMarkup:
    """Build keyboard to pick a meal to delete."""
    rows: List[List[InlineKeyboardButton]] = []
    # Show up to last 8 meals
    for m in reversed(meals[-8:]):
        cals = m.get("calories", 0)
        title = m.get("title", "Еда")
        rows.append([
            InlineKeyboardButton(
                text=f"❌ {title} ({cals} ккал)",
                callback_data=f"nutr_del:{m['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="nutr_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
