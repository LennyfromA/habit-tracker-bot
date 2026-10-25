"""Middleware for user registration verification and timezone injection."""

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.database import users_repo


class UserCheckMiddleware(BaseMiddleware):
    """
    Ensures user is registered in the database, injects user record
    and calculated local datetime metadata into handler parameters.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        from_user = None
        if isinstance(event, Message):
            from_user = event.from_user
        elif isinstance(event, CallbackQuery):
            from_user = event.from_user

        if not from_user or from_user.is_bot:
            return await handler(event, data)

        user_id = from_user.id
        user = await users_repo.get_user(user_id)

        # Calculate current time in UTC
        now_utc = datetime.now(timezone.utc)

        if user:
            utc_offset = user.get("utc_offset", 3)
            local_now = now_utc + timedelta(hours=utc_offset)
            data["user_data"] = user
            data["utc_offset"] = utc_offset
            data["local_now"] = local_now
            data["local_date"] = local_now.strftime("%Y-%m-%d")
            data["local_weekday"] = local_now.isoweekday()
        else:
            # New user: default to UTC+3
            data["user_data"] = None
            data["utc_offset"] = 3
            data["local_now"] = now_utc + timedelta(hours=3)
            data["local_date"] = (now_utc + timedelta(hours=3)).strftime("%Y-%m-%d")
            data["local_weekday"] = (now_utc + timedelta(hours=3)).isoweekday()

            # Check if this is not a /start command or onboarding callback
            is_start_cmd = (
                isinstance(event, Message)
                and event.text
                and event.text.startswith("/start")
            )
            is_onboarding_cb = (
                isinstance(event, CallbackQuery)
                and event.data
                and (event.data.startswith("set_tz:") or event.data.startswith("set_ci:"))
            )

            # Check FSM state for onboarding
            state = data.get("state")
            current_state = await state.get_state() if state else None
            is_in_onboarding = current_state and "OnboardingStates" in current_state

            if not (is_start_cmd or is_onboarding_cb or is_in_onboarding):
                if isinstance(event, Message):
                    await event.answer(
                        "👋 Добро пожаловать! Давайте настроим ваш часовой пояс перед началом работы.\n\n"
                        "Нажмите /start, чтобы начать."
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer("Пожалуйста, отправьте /start для начала работы.", show_alert=True)
                return None

        return await handler(event, data)
