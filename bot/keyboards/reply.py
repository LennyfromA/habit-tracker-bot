"""Persistent reply keyboards for the main bot menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Build compact persistent main navigation keyboard (Health, Habits, Settings)."""
    keyboard = [
        [
            KeyboardButton(text="🍏 Здоровье"),
            KeyboardButton(text="📋 Привычки"),
        ],
        [
            KeyboardButton(text="⚙️ Настройки"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел...",
    )
