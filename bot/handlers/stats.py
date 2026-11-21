"""Handler for productivity statistics, dark-themed charts, and markdown exports."""

import asyncio
import html
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from bot.database import habits_repo, users_repo
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.chart_builder import build_weekly_stats_chart
from bot.services.widget_launcher import is_widget_enabled, send_widget_card

logger = logging.getLogger(__name__)
router = Router(name="stats_router")


async def render_stats_view(target_message: Message, user_id: int, local_date: str) -> None:
    """Render productivity stats and dark-themed chart to target_message using explicit user_id."""
    end_dt = datetime.strptime(local_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=6)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    stats = await habits_repo.get_weekly_stats(user_id, start_date_str, local_date)
    daily_stats = stats["daily"]
    habits_stats = stats["habits"]

    total_week_minutes = sum(daily_stats.values())
    total_hours = total_week_minutes // 60
    rem_minutes = total_week_minutes % 60

    # Build HTML text summary
    summary_lines = [
        f"📊 <b>Отчет продуктивности за 7 дней</b> ({start_dt.strftime('%d.%m')} — {end_dt.strftime('%d.%m')})\n",
        f"⏱ <b>Всего в фокусе:</b> {total_hours} ч {rem_minutes} мин ({total_week_minutes} мин)",
        f"📅 <b>Активных дней:</b> {len(daily_stats)} из 7\n",
        "<b>Привычки и стрики:</b>",
    ]

    if not habits_stats:
        summary_lines.append("У вас пока нет привычек для отслеживания статистики.")
    else:
        for h in habits_stats:
            escaped_h_name = html.escape(h["name"])
            active_days_set = set(int(d) for d in h["active_days"].split(",") if d.isdigit())
            scheduled_days = sum(
                1 for i in range(7)
                if (start_dt + timedelta(days=i)).isoweekday() in active_days_set
            )
            completed_days = h.get("completed_days", 0)
            rate = round((completed_days / scheduled_days) * 100) if scheduled_days > 0 else 0

            summary_lines.append(
                f"• <b>{escaped_h_name}</b>: {rate}% выполнено ({completed_days}/{scheduled_days} дн.) | "
                f"🔥 Стрик: {h['streak_days']} дн."
            )

    caption_text = "\n".join(summary_lines)

    # Render dark-themed chart in worker thread so it does not block the asyncio event loop
    chart_buffer = await asyncio.to_thread(build_weekly_stats_chart, daily_stats, habits_stats, local_date)
    photo_file = BufferedInputFile(
        file=chart_buffer.getvalue(),
        filename=f"weekly_stats_{local_date}.png",
    )

    # Telegram caption character limit is 1024
    if len(caption_text) <= 1024:
        await target_message.answer_photo(
            photo=photo_file,
            caption=caption_text,
            reply_markup=get_main_reply_keyboard(),
        )
    else:
        short_title = f"📊 <b>Отчет продуктивности за 7 дней</b> ({start_dt.strftime('%d.%m')} — {end_dt.strftime('%d.%m')})"
        await target_message.answer_photo(
            photo=photo_file,
            caption=short_title,
        )
        await target_message.answer(
            caption_text,
            reply_markup=get_main_reply_keyboard(),
        )


async def render_export_view(target_message: Message, user_id: int, local_date: str) -> None:
    """Generate and send markdown (.md) productivity report document using explicit user_id."""
    end_dt = datetime.strptime(local_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=6)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    data = await habits_repo.get_export_data(user_id, start_date_str, local_date)

    if not data:
        await target_message.answer(
            "📁 У вас пока нет привычек для формирования отчета.",
            reply_markup=get_main_reply_keyboard(),
        )
        return

    # Build Markdown document
    md_lines = [
        f"# Отчет продуктивности: {start_dt.strftime('%d.%m')} - {end_dt.strftime('%d.%m')}\n",
    ]

    for item in data:
        habit = item["habit"]
        htype = habit.get("habit_type", "duration")
        logs_map = {l["logged_date"]: l for l in item["logs"]}
        active_days_set = set(int(d) for d in habit["active_days"].split(",") if d.isdigit())

        md_lines.append(f"## {habit['name']} (Стрик: {habit['streak_days']} дней)")

        # Iterate over all 7 days
        for i in range(7):
            current_day = start_dt + timedelta(days=i)
            day_str = current_day.strftime("%Y-%m-%d")

            # Only include if scheduled or if log exists
            if current_day.isoweekday() in active_days_set or day_str in logs_map:
                log = logs_map.get(day_str)
                if htype == "checklist":
                    tgt = habit.get("target_repeats", 1) or 1
                    cur = log.get("completed_repeats", 0) if log else 0
                    is_done = bool(log.get("is_completed")) if log else False
                    notes = log.get("notes") if log else ""
                    notes_str = f" — {notes}" if notes else ""
                    if is_done:
                        md_lines.append(f"- [x] {day_str}: {cur}/{tgt} раз{notes_str}")
                    else:
                        md_lines.append(f"- [ ] {day_str}: {cur}/{tgt} раз{notes_str}")
                else:
                    if log:
                        spent = log["spent_minutes"]
                        target = habit["target_minutes"]
                        is_done = bool(log["is_completed"])
                        notes = log.get("notes")
                        notes_str = f" — {notes}" if notes else ""

                        if is_done:
                            md_lines.append(f"- [x] {day_str}: {spent} мин{notes_str}")
                        else:
                            md_lines.append(f"- [ ] {day_str}: {spent}/{target} мин{notes_str}")
                    else:
                        md_lines.append(f"- [ ] {day_str}: 0/{habit['target_minutes']} мин")

        md_lines.append("")  # Empty line between habits

    md_content = "\n".join(md_lines).encode("utf-8")
    doc_file = BufferedInputFile(
        file=md_content,
        filename=f"productivity_report_{start_dt.strftime('%d_%m')}_{end_dt.strftime('%d_%m')}.md",
    )

    await target_message.answer_document(
        document=doc_file,
        caption="📁 Ваш отчет продуктивности за последние 7 дней в формате Markdown.",
        reply_markup=get_main_reply_keyboard(),
    )


@router.message(F.text == "📊 Статистика")
@router.message(Command("stats"))
async def cmd_stats(message: Message, local_date: str) -> None:
    """Send text summary and dark-themed matplotlib chart for the last 7 days."""
    if is_widget_enabled():
        user = await users_repo.get_user(message.from_user.id)
        offset = user.get("utc_offset", 3) if user else 3
        local_now = datetime.strptime(local_date, "%Y-%m-%d")
        await send_widget_card(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            view="stats",
            local_date=local_date,
            local_weekday=local_now.isoweekday(),
            utc_offset=offset,
            reply_markup=get_main_reply_keyboard(),
        )
        return

    await render_stats_view(target_message=message, user_id=message.from_user.id, local_date=local_date)


@router.message(F.text == "📁 Экспорт")
@router.message(Command("export"))
async def cmd_export_markdown(message: Message, local_date: str) -> None:
    """Generate and send markdown (.md) productivity report document."""
    await render_export_view(target_message=message, user_id=message.from_user.id, local_date=local_date)


# Backward-compatible alias
cmd_export = cmd_export_markdown
