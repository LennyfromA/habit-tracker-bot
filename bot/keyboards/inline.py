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


def get_habit_type_keyboard() -> InlineKeyboardMarkup:
    """Select between duration-based habit and repetition/checklist habit."""
    keyboard = [
        [
            InlineKeyboardButton(text="⏱ На время (с таймером)", callback_data="set_htype:duration"),
        ],
        [
            InlineKeyboardButton(text="✅ Чек-лист / галочка (таблетки, уход...)", callback_data="set_htype:checklist"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_checklist_repeats_keyboard() -> InlineKeyboardMarkup:
    """Select repetitions count per day for checklist habit."""
    keyboard = [
        [
            InlineKeyboardButton(text="1 раз в день", callback_data="set_hreps:1"),
            InlineKeyboardButton(text="2 раза (утро / вечер)", callback_data="set_hreps:2"),
        ],
        [
            InlineKeyboardButton(text="3 раза в день", callback_data="set_hreps:3"),
            InlineKeyboardButton(text="✍️ Своё число", callback_data="set_hreps:custom"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_checklist_reminders_keyboard(repeats: int = 1) -> InlineKeyboardMarkup:
    """Select reminder time presets or skip."""
    rows = []
    if repeats == 1:
        rows.append([
            InlineKeyboardButton(text="🌅 Утро (09:00)", callback_data="set_hrem:09:00"),
            InlineKeyboardButton(text="🌙 Вечер (21:00)", callback_data="set_hrem:21:00"),
        ])
    elif repeats == 2:
        rows.append([
            InlineKeyboardButton(text="🌅 09:00 + 🌙 21:00", callback_data="set_hrem:09:00,21:00"),
        ])
        rows.append([
            InlineKeyboardButton(text="🌅 08:30 + 🌙 20:30", callback_data="set_hrem:08:30,20:30"),
        ])
    elif repeats == 3:
        rows.append([
            InlineKeyboardButton(text="09:00, 14:00, 21:00", callback_data="set_hrem:09:00,14:00,21:00"),
        ])

    rows.append([
        InlineKeyboardButton(text="🔕 Без напоминаний", callback_data="set_hrem:none"),
        InlineKeyboardButton(text="✍️ Своё время", callback_data="set_hrem:custom"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_habit_reminder_push_keyboard(habit_id: int) -> InlineKeyboardMarkup:
    """Buttons sent with interactive reminder notification."""
    keyboard = [
        [
            InlineKeyboardButton(text="✅ Выполнено (+1)", callback_data=f"remind_check:{habit_id}"),
            InlineKeyboardButton(text="⏰ Через 15 мин", callback_data=f"remind_snooze:{habit_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_habit_actions_keyboard(
    habit_id: int,
    remaining_minutes: int,
    is_completed: bool,
    is_frozen_today: bool,
    can_freeze: bool,
    habit_type: str = "duration",
    completed_repeats: int = 0,
    target_repeats: int = 1,
) -> InlineKeyboardMarkup:
    """Keyboard for a single habit in habit list."""
    buttons: List[List[InlineKeyboardButton]] = []

    if habit_type == "checklist":
        if is_completed:
            btn_text = f"🎉 Выполнено ({completed_repeats}/{target_repeats}) 🎉"
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"already_done:{habit_id}")])
        else:
            btn_text = f"✅ Отметить выполнение ({completed_repeats}/{target_repeats})"
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"habit_check:{habit_id}")])
    else:
        if is_completed:
            timer_text = "🎉 Выполнено на сегодня! (Ещё фокус?)"
        elif remaining_minutes > 0:
            timer_text = f"▶ Запустить таймер (Остаток: {remaining_minutes} мин)"
        else:
            timer_text = "▶ Запустить таймер"
        buttons.append([InlineKeyboardButton(text=timer_text, callback_data=f"timer_start:{habit_id}")])

    second_row = [
        InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_habit_menu:{habit_id}"),
    ]
    if is_frozen_today:
        second_row.append(
            InlineKeyboardButton(text="❄️ Заморожено", callback_data="already_frozen")
        )
    elif can_freeze:
        second_row.append(
            InlineKeyboardButton(text="❄️ Заморозить", callback_data=f"freeze_habit:{habit_id}")
        )

    second_row.append(
        InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_habit:{habit_id}")
    )
    buttons.append(second_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_habit_edit_menu_keyboard(habit_id: int, habit_type: str = "duration") -> InlineKeyboardMarkup:
    """Menu with properties to edit for a habit."""
    if habit_type == "checklist":
        keyboard = [
            [
                InlineKeyboardButton(text="📝 Название", callback_data=f"edit_h_name:{habit_id}"),
                InlineKeyboardButton(text="🎯 Кол-во раз", callback_data=f"edit_h_reps:{habit_id}"),
            ],
            [
                InlineKeyboardButton(text="⏰ Напоминания", callback_data=f"edit_h_rem:{habit_id}"),
                InlineKeyboardButton(text="📅 Дни недели", callback_data=f"edit_h_days:{habit_id}"),
            ],
            [
                InlineKeyboardButton(text="🔙 Назад к привычкам", callback_data="habits_refresh"),
            ],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(text="📝 Название", callback_data=f"edit_h_name:{habit_id}"),
                InlineKeyboardButton(text="⏱ Время (цель)", callback_data=f"edit_h_time:{habit_id}"),
            ],
            [
                InlineKeyboardButton(text="📅 Дни недели / напоминания", callback_data=f"edit_h_days:{habit_id}"),
            ],
            [
                InlineKeyboardButton(text="🔙 Назад к привычкам", callback_data="habits_refresh"),
            ],
        ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_edit_checklist_repeats_keyboard(habit_id: int) -> InlineKeyboardMarkup:
    """Keyboard to edit repeats count of checklist habit."""
    keyboard = [
        [
            InlineKeyboardButton(text="1 раз в день", callback_data=f"set_edit_reps:{habit_id}:1"),
            InlineKeyboardButton(text="2 раза (утро / вечер)", callback_data=f"set_edit_reps:{habit_id}:2"),
        ],
        [
            InlineKeyboardButton(text="3 раза в день", callback_data=f"set_edit_reps:{habit_id}:3"),
            InlineKeyboardButton(text="✍️ Своё число", callback_data=f"custom_edit_reps:{habit_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data=f"edit_habit_menu:{habit_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_edit_checklist_reminders_keyboard(habit_id: int, repeats: int = 1) -> InlineKeyboardMarkup:
    """Keyboard to edit reminder times of checklist habit."""
    rows = []
    if repeats == 1:
        rows.append([
            InlineKeyboardButton(text="🌅 Утро (09:00)", callback_data=f"set_edit_rem:{habit_id}:09:00"),
            InlineKeyboardButton(text="🌙 Вечер (21:00)", callback_data=f"set_edit_rem:{habit_id}:21:00"),
        ])
    elif repeats == 2:
        rows.append([
            InlineKeyboardButton(text="🌅 09:00 + 🌙 21:00", callback_data=f"set_edit_rem:{habit_id}:09:00,21:00"),
        ])
        rows.append([
            InlineKeyboardButton(text="🌅 08:30 + 🌙 20:30", callback_data=f"set_edit_rem:{habit_id}:08:30,20:30"),
        ])
    elif repeats == 3:
        rows.append([
            InlineKeyboardButton(text="09:00, 14:00, 21:00", callback_data=f"set_edit_rem:{habit_id}:09:00,14:00,21:00"),
        ])

    rows.append([
        InlineKeyboardButton(text="🔕 Без напоминаний", callback_data=f"set_edit_rem:{habit_id}:none"),
        InlineKeyboardButton(text="✍️ Своё время", callback_data=f"custom_edit_rem:{habit_id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"edit_habit_menu:{habit_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_habit_time_presets_keyboard(habit_id: int) -> InlineKeyboardMarkup:
    """Presets for habit daily target minutes."""
    keyboard = [
        [
            InlineKeyboardButton(text="15 мин", callback_data=f"set_h_time:{habit_id}:15"),
            InlineKeyboardButton(text="25 мин (Помодоро)", callback_data=f"set_h_time:{habit_id}:25"),
        ],
        [
            InlineKeyboardButton(text="30 мин", callback_data=f"set_h_time:{habit_id}:30"),
            InlineKeyboardButton(text="45 мин", callback_data=f"set_h_time:{habit_id}:45"),
        ],
        [
            InlineKeyboardButton(text="60 мин (1 час)", callback_data=f"set_h_time:{habit_id}:60"),
            InlineKeyboardButton(text="90 мин", callback_data=f"set_h_time:{habit_id}:90"),
        ],
        [
            InlineKeyboardButton(text="✍️ Ввести своё время", callback_data=f"custom_h_time:{habit_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data=f"edit_habit_menu:{habit_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_edit_days_selector_keyboard(habit_id: int, selected_days: Set[int]) -> InlineKeyboardMarkup:
    """Build day-of-week toggle keyboard for habit editing."""
    row1 = [
        InlineKeyboardButton(
            text=f"{DAY_LABELS[d]} {'✅' if d in selected_days else '◻️'}",
            callback_data=f"toggle_edit_day:{habit_id}:{d}",
        )
        for d in [1, 2, 3, 4]
    ]

    row2 = [
        InlineKeyboardButton(
            text=f"{DAY_LABELS[d]} {'✅' if d in selected_days else '◻️'}",
            callback_data=f"toggle_edit_day:{habit_id}:{d}",
        )
        for d in [5, 6, 7]
    ]

    row3 = [
        InlineKeyboardButton(text="📅 Каждый день", callback_data=f"preset_edit_days:{habit_id}:all"),
        InlineKeyboardButton(text="💼 Будни", callback_data=f"preset_edit_days:{habit_id}:weekdays"),
        InlineKeyboardButton(text="🏖 Выходные", callback_data=f"preset_edit_days:{habit_id}:weekends"),
    ]

    row4 = [
        InlineKeyboardButton(text="Сохранить дни ✔️", callback_data=f"save_edit_days:{habit_id}"),
    ]

    row5 = [
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"edit_habit_menu:{habit_id}"),
    ]

    return InlineKeyboardMarkup(inline_keyboard=[row1, row2, row3, row4, row5])


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
            if h.get("habit_type") == "checklist":
                cur = h.get("completed_repeats", 0)
                tgt = h.get("target_repeats", 1) or 1
                btn_text = f"✅ Отметить: {h['name']} ({cur}/{tgt})"
            else:
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


def get_habits_hub_keyboard() -> InlineKeyboardMarkup:
    """Action buttons for Habits section."""
    keyboard = [
        [
            InlineKeyboardButton(text="➕ Новая привычка", callback_data="habits_hub:new"),
            InlineKeyboardButton(text="⏱ Таймер фокуса", callback_data="habits_hub:timer"),
        ],
        [
            InlineKeyboardButton(text="🌙 Вечерний чек-ин", callback_data="habits_hub:checkin"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="habits_hub:stats"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_settings_hub_keyboard() -> InlineKeyboardMarkup:
    """Action buttons for Settings section."""
    keyboard = [
        [
            InlineKeyboardButton(text="🔔 Все уведомления", callback_data="settings_notifications"),
        ],
        [
            InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="settings_timezone"),
            InlineKeyboardButton(text="📁 Экспорт отчета", callback_data="settings_export"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_notifications_hub_keyboard(is_muted: bool) -> InlineKeyboardMarkup:
    """Action buttons for Notifications control."""
    if is_muted:
        toggle_btn = InlineKeyboardButton(text="🔔 Включить уведомления", callback_data="notif_unmute")
    else:
        toggle_btn = InlineKeyboardButton(text="🔕 Отключить все уведомления", callback_data="notif_mute_menu")

    keyboard = [
        [toggle_btn],
        [
            InlineKeyboardButton(text="⏰ Время чек-ина", callback_data="settings_ci_time"),
            InlineKeyboardButton(text="🌅 Взвешивание", callback_data="weight_choose_time"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад в настройки", callback_data="settings_main"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_mute_duration_keyboard() -> InlineKeyboardMarkup:
    """Duration selector for muting notifications."""
    keyboard = [
        [
            InlineKeyboardButton(text="⏳ На 1 час", callback_data="notif_mute:3600"),
            InlineKeyboardButton(text="📅 На 1 день (24 ч)", callback_data="notif_mute:86400"),
        ],
        [
            InlineKeyboardButton(text="🗓 На 1 неделю", callback_data="notif_mute:604800"),
            InlineKeyboardButton(text="♾ Навсегда", callback_data="notif_mute:forever"),
        ],
        [
            InlineKeyboardButton(text="🔙 Отмена", callback_data="settings_notifications"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
