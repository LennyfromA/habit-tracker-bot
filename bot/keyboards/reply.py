"""Persistent reply keyboards for the main bot menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Build persistent main navigation keyboard."""
    keyboard = [
        [
            KeyboardButton(text="📋 Мои привычки"),
            KeyboardButton(text="⏱ Активный таймер"),
        ],
        [
            KeyboardButton(text="➕ Новая привычка"),
            KeyboardButton(text="🌙 Вечерний чек-ин"),
        ],
        [
            KeyboardButton(text="🥗 Питание"),
            KeyboardButton(text="📊 Статистика"),
        ],
        [
            KeyboardButton(text="📁 Экспорт"),
            KeyboardButton(text="⚙️ Настройки"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие или команду...",
    )
