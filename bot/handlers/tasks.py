"""Handler for managing tasks: quick creation, listing, completion, and rescheduling."""

import html
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.database import tasks_repo, timers_repo, users_repo
from bot.services.task_parser import parse_quick_task
from bot.services.widget_launcher import is_widget_enabled, send_widget_card, sync_widget_message

logger = logging.getLogger(__name__)
router = Router(name="tasks_router")


def get_user_local_context(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate user's local date and datetime based on utc_offset."""
    utc_offset = user.get("utc_offset", 3) if user else 3
    local_now = datetime.utcnow() + timedelta(hours=utc_offset)
    return {
        "utc_offset": utc_offset,
        "local_now": local_now,
        "local_date": local_now.strftime("%Y-%m-%d"),
        "local_time": local_now.strftime("%H:%M"),
    }


@router.message(Command(commands=["t", "task", "tasks"]))
async def cmd_tasks(message: Message) -> None:
    """
    Handle /t, /task, /tasks commands.
    If arguments provided: parse and create a quick task.
    If no arguments: show tasks list or launch widget view 'tasks'.
    """
    user_id = message.from_user.id
    user = await users_repo.get_user(user_id)
    ctx = get_user_local_context(user)
    local_date = ctx["local_date"]

    raw_args = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            raw_args = parts[1].strip()

    if raw_args:
        # Quick-add task mode
        parsed = parse_quick_task(raw_args, local_date)
        if not parsed:
            await message.answer(
                "❌ <b>Не удалось распознать задачу.</b>\n\n"
                "Пример использования:\n"
                "<code>/t Позвонить врачу завтра 14:00 #здоровье 25m</code>\n"
                "<code>/t Купить молоко #дом сегодня</code>\n"
                "<code>/t Зарядка 15мин каждый день</code>"
            )
            return

        # Resolve list
        list_name = parsed.get("list_name")
        if list_name:
            target_list = await tasks_repo.get_list_by_name(user_id, list_name)
            if not target_list:
                list_id = await tasks_repo.create_list(user_id, list_name, icon="📁")
                list_title = f"📁 {list_name}"
            else:
                list_id = target_list["id"]
                list_title = f"{target_list.get('icon', '📁')} {target_list['name']}"
        else:
            inbox = await tasks_repo.ensure_inbox_list(user_id)
            list_id = inbox["id"]
            list_title = "📥 Входящие"

        task_id = await tasks_repo.create_task(
            user_id=user_id,
            list_id=list_id,
            title=parsed["title"],
            due_date=parsed.get("due_date"),
            due_time=parsed.get("due_time"),
            recurrence=parsed.get("recurrence", "none"),
            estimated_minutes=parsed.get("estimated_minutes"),
        )

        # Build pretty confirmation
        details = []
        if parsed.get("due_date"):
            d_val = parsed["due_date"]
            if d_val == local_date:
                details.append("📅 Сегодня")
            elif d_val == (ctx["local_now"].date() + timedelta(days=1)).strftime("%Y-%m-%d"):
                details.append("📅 Завтра")
            else:
                details.append(f"📅 {d_val}")

        if parsed.get("due_time"):
            details.append(f"⏰ {parsed['due_time']}")

        if parsed.get("estimated_minutes"):
            details.append(f"⏱ {parsed['estimated_minutes']} мин")

        if parsed.get("recurrence") != "none":
            rec_map = {"daily": "🔄 Каждый день", "weekdays": "🔄 По будням", "weekly": "🔄 Еженедельно"}
            details.append(rec_map.get(parsed["recurrence"], parsed["recurrence"]))

        details_str = " | ".join(details)
        if details_str:
            details_str = f"\n▫️ {details_str}"

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="⏱ Начать фокус", callback_data=f"task_focus:{task_id}"),
                    InlineKeyboardButton(text="✅ Выполнено", callback_data=f"task_rem_done:{task_id}"),
                ]
            ]
        )

        await message.answer(
            f"✅ <b>Задача добавлена в {html.escape(list_title)}:</b>\n"
            f"<b>«{html.escape(parsed['title'])}»</b>{details_str}",
            reply_markup=kb,
        )

        if is_widget_enabled():
            await sync_widget_message(message.bot, user_id)
        return

    # No arguments mode: show tasks list or open widget
    if is_widget_enabled():
        await send_widget_card(message, user_id, view_type="tasks")
        return

    # Fallback text mode when widget is disabled
    today_tasks = await tasks_repo.get_today_tasks(user_id, local_date)
    if not today_tasks:
        await message.answer(
            "📋 <b>Ваш список задач пуст на сегодня.</b>\n\n"
            "Добавьте первую задачу командой:\n"
            "<code>/t Название задачи #список завтра 15:00 25m</code>"
        )
        return

    lines = ["📋 <b>Задачи на сегодня:</b>\n"]
    kb_rows = []
    for t in today_tasks[:10]:
        status_icon = "✅" if t["is_completed"] else "▫️"
        overdue_badge = " ⚠️ [Просрочено]" if t.get("is_overdue") and not t["is_completed"] else ""
        time_badge = f" ({t['due_time']})" if t.get("due_time") else ""
        lines.append(f"{status_icon} <b>{html.escape(t['title'])}</b>{time_badge}{overdue_badge}")

        if not t["is_completed"]:
            kb_rows.append([
                InlineKeyboardButton(text=f"✅ {t['title'][:20]}", callback_data=f"task_rem_done:{t['id']}"),
                InlineKeyboardButton(text="⏱ Фокус", callback_data=f"task_focus:{t['id']}"),
            ])

    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None)


@router.callback_query(F.data.startswith("task_rem_done:"))
async def cb_task_done(callback: CallbackQuery) -> None:
    """Mark task completed from inline notification or chat list."""
    task_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user = await users_repo.get_user(user_id)
    ctx = get_user_local_context(user)

    task = await tasks_repo.get_task_by_id(task_id, user_id)
    if not task:
        await callback.answer("Задача не найдена или уже удалена.", show_alert=True)
        return

    new_state = await tasks_repo.toggle_task_complete(task_id, user_id, ctx["local_date"])
    status_text = "✅ Задача выполнена!" if new_state else "▫️ Задача возвращена в активные"
    await callback.answer(status_text)

    # Edit message to reflect completion
    try:
        orig_text = callback.message.html_text or callback.message.text or ""
        updated_text = f"<s>{orig_text}</s>\n\n<b>✅ Отмечено выполненным!</b>"
        await callback.message.edit_text(updated_text, reply_markup=None)
    except TelegramBadRequest:
        pass

    if is_widget_enabled():
        await sync_widget_message(callback.bot, user_id)


@router.callback_query(F.data.startswith("task_rem_tomorrow:"))
async def cb_task_tomorrow(callback: CallbackQuery) -> None:
    """Postpone task to tomorrow from inline reminder notification."""
    task_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user = await users_repo.get_user(user_id)
    ctx = get_user_local_context(user)

    task = await tasks_repo.get_task_by_id(task_id, user_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    tomorrow_date = (ctx["local_now"].date() + timedelta(days=1)).strftime("%Y-%m-%d")
    await tasks_repo.reschedule_task(task_id, user_id, tomorrow_date)
    await callback.answer("📅 Задача перенесена на завтра!")

    try:
        orig_text = callback.message.html_text or callback.message.text or ""
        updated_text = f"{orig_text}\n\n<i>📅 Перенесено на завтра ({tomorrow_date})</i>"
        await callback.message.edit_text(updated_text, reply_markup=None)
    except TelegramBadRequest:
        pass

    if is_widget_enabled():
        await sync_widget_message(callback.bot, user_id)


@router.callback_query(F.data.startswith("task_focus:"))
async def cb_task_focus(callback: CallbackQuery) -> None:
    """Start focus timer directly for a task from chat button."""
    task_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    task = await tasks_repo.get_task_by_id(task_id, user_id)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    duration_minutes = task.get("estimated_minutes") or 25
    target_seconds = duration_minutes * 60

    await timers_repo.create_or_replace_timer(
        user_id=user_id,
        task_id=task_id,
        total_target_seconds=target_seconds,
        message_id=callback.message.message_id,
        chat_id=callback.message.chat.id,
    )

    await callback.answer("⏱ Таймер фокуса запущен!")
    await callback.message.answer(
        f"⏱ <b>Фокус-таймер запущен для задачи:</b>\n"
        f"«{html.escape(task['title'])}»\n"
        f"⏳ Длительность: <b>{duration_minutes} мин</b>\n\n"
        f"Отслеживайте прогресс в виджете или боте."
    )

    if is_widget_enabled():
        await sync_widget_message(callback.bot, user_id)


@router.callback_query(F.data == "checkin_defer_tasks")
async def cb_checkin_defer_tasks(callback: CallbackQuery) -> None:
    """Evening check-in action: move all uncompleted tasks from today to tomorrow."""
    user_id = callback.from_user.id
    user = await users_repo.get_user(user_id)
    ctx = get_user_local_context(user)

    count = await tasks_repo.defer_today_tasks_to_tomorrow(user_id, ctx["local_date"])
    if count > 0:
        await callback.answer(f"📅 Перенесено задач на завтра: {count}", show_alert=True)
    else:
        await callback.answer("Нет незакрытых задач для переноса 👍", show_alert=True)

    if is_widget_enabled():
        await sync_widget_message(callback.bot, user_id)
