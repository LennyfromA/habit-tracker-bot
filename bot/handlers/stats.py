"""Handler for productivity statistics, dark-themed charts, and markdown exports."""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from bot.database import habits_repo
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.chart_builder import build_weekly_stats_chart

router = Router(name="stats_router")


@router.message(F.text == "📊 Статистика")
@router.message(Command("stats"))
async def cmd_stats(message: Message, local_date: str) -> None:
    """Send text summary and dark-themed matplotlib chart for the last 7 days."""
    user_id = message.from_user.id
    end_dt = datetime.strptime(local_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=6)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    stats = await habits_repo.get_weekly_stats(user_id, start_date_str, local_date)
    daily_stats = stats["daily"]
    habits_stats = stats["habits"]

    total_week_minutes = sum(daily_stats.values())
    total_hours = total_week_minutes // 60
    rem_minutes = total_week_minutes % 60

    # Build text summary
    summary_lines = [
        f"📊 **Отчет продуктивности за 7 дней** ({start_dt.strftime('%d.%m')} — {end_dt.strftime('%d.%m')})\n",
        f"⏱ **Всего в фокусе:** {total_hours} ч {rem_minutes} мин ({total_week_minutes} мин)",
        f"📅 **Активных дней:** {len(daily_stats)} из 7\n",
        "**Привычки и стрики:**",
    ]

    if not habits_stats:
        summary_lines.append("У вас пока нет привычек для отслеживания статистики.")
    else:
        # Calculate completion rate for each habit
        for h in habits_stats:
            active_days_set = set(int(d) for d in h["active_days"].split(",") if d.isdigit())
            scheduled_days = sum(
                1 for i in range(7)
                if (start_dt + timedelta(days=i)).isoweekday() in active_days_set
            )
            completed_days = h.get("completed_days", 0)
            rate = round((completed_days / scheduled_days) * 100) if scheduled_days > 0 else 0

            summary_lines.append(
                f"• **{h['name']}**: {rate}% выполнено ({completed_days}/{scheduled_days} дн.) | "
                f"🔥 Стрик: {h['streak_days']} дн."
            )

    caption_text = "\n".join(summary_lines)

    # Render dark-themed chart in memory
    chart_buffer = build_weekly_stats_chart(daily_stats, habits_stats, local_date)
    photo_file = BufferedInputFile(
        file=chart_buffer.getvalue(),
        filename=f"weekly_stats_{local_date}.png",
    )

    await message.answer_photo(
        photo=photo_file,
        caption=caption_text,
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown",
    )


@router.message(F.text == "📁 Экспорт")
@router.message(Command("export"))
async def cmd_export_markdown(message: Message, local_date: str) -> None:
    """Generate and send markdown (.md) productivity report document."""
    user_id = message.from_user.id
    end_dt = datetime.strptime(local_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=6)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    data = await habits_repo.get_export_data(user_id, start_date_str, local_date)

    if not data:
        await message.answer(
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

    await message.answer_document(
        document=doc_file,
        caption="📁 Ваш отчет продуктивности за последние 7 дней в формате Markdown.",
        reply_markup=get_main_reply_keyboard(),
    )
