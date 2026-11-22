"""Handler for interactive evening check-in dashboard."""

import html
import logging
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.database import habits_repo, nutrition_repo, tasks_repo, users_repo, water_repo
from bot.keyboards.inline import get_checkin_dashboard_keyboard
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.widget_launcher import is_widget_enabled, send_widget_card

logger = logging.getLogger(__name__)
router = Router(name="checkin_router")


def format_checkin_text(
    habits: List[Dict[str, Any]],
    local_date: str,
    nutrition_summary: Optional[Dict[str, Any]] = None,
    nutrition_goals: Optional[Dict[str, Any]] = None,
    water_ml: int = 0,
    water_target: int = 2000,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Format check-in status message in HTML."""
    total_count = len(habits)
    completed_count = sum(1 for h in habits if h.get("is_completed"))
    frozen_count = sum(1 for h in habits if h.get("is_frozen_today"))

    lines = [
        "🌙 <b>Вечерний чек-ин продуктивности!</b>",
        f"📅 Дата: <code>{local_date}</code>",
        f"📊 Привычки: <b>{completed_count + frozen_count}/{total_count}</b> выполнено/заморожено\n",
    ]

    for h in habits:
        h_name = html.escape(h["name"])
        if h.get("habit_type") == "checklist":
            cur = h.get("completed_repeats", 0)
            tgt = h.get("target_repeats", 1) or 1
            if h.get("is_completed"):
                lines.append(f"✅ «{h_name}» — {cur}/{tgt} раз (Выполнено)")
            elif h.get("is_frozen_today"):
                lines.append(f"❄️ «{h_name}» — Заморожено сегодня")
            else:
                remaining = max(0, tgt - cur)
                lines.append(f"⏳ «{h_name}» — {cur}/{tgt} раз (Осталось: {remaining})")
        else:
            spent = h.get("spent_minutes", 0)
            target = h["target_minutes"]
            if h.get("is_completed"):
                lines.append(f"✅ «{h_name}» — {spent}/{target} мин (Выполнено)")
            elif h.get("is_frozen_today"):
                lines.append(f"❄️ «{h_name}» — Заморожено сегодня")
            else:
                remaining = max(0, target - spent)
                lines.append(f"⏳ «{h_name}» — {spent}/{target} мин (Осталось: {remaining} мин)")

    track_bju = bool((nutrition_goals or {}).get("track_bju", 1))
    track_water = bool((nutrition_goals or {}).get("track_water", 1))

    if nutrition_summary and nutrition_summary.get("meal_count", 0) > 0:
        cals = nutrition_summary.get("total_calories", 0)
        target = (nutrition_goals or {}).get("target_calories", 2000)
        diff = target - cals
        diff_str = f"осталось {diff} ккал" if diff >= 0 else f"+{abs(diff)} ккал сверх нормы"
        macros_part = ""
        if track_bju:
            p = nutrition_summary.get("total_protein", 0)
            f = nutrition_summary.get("total_fat", 0)
            c = nutrition_summary.get("total_carbs", 0)
            if p > 0 or f > 0 or c > 0:
                macros_part = f" ({p}Б / {f}Ж / {c}У)"
        lines.append(f"\n🥗 <b>Питание за сегодня:</b> {cals}/{target} ккал ({diff_str}){macros_part}")

    if track_water and water_ml > 0:
        lines.append(f"💧 <b>Вода за сегодня:</b> {water_ml}/{water_target} мл")

    if tasks is not None and len(tasks) > 0:
        t_completed = sum(1 for t in tasks if t.get("is_completed"))
        lines.append(f"\n📌 <b>Задачи на сегодня:</b> {t_completed}/{len(tasks)} закрыто")
        open_tasks = [t for t in tasks if not t.get("is_completed")]
        for ot in open_tasks[:5]:
            time_part = f" ({ot['due_time']})" if ot.get("due_time") else ""
            lines.append(f"▫️ {html.escape(ot['title'])}{time_part}")

    incomplete = [h for h in habits if not h.get("is_completed") and not h.get("is_frozen_today")]
    if incomplete:
        lines.append("\nНажмите на привычку, чтобы быстро отметить её выполнение:")
    else:
        lines.append("\n🎉 Все запланированные привычки на сегодня закрыты! Отличный результат!")

    return "\n".join(lines)


async def render_checkin_view(
    target_message: Message,
    user_id: int,
    local_date: str,
    local_weekday: int,
) -> None:
    """Render checkin view to target_message using explicit user_id."""
    habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)

    if not habits:
        await target_message.answer(
            "🌴 На сегодня у вас не было запланированных привычек. Отдыхайте и набирайтесь сил перед завтрашним днем!",
            reply_markup=get_main_reply_keyboard(),
        )
        return

    nutr_summary = await nutrition_repo.get_daily_summary(user_id, local_date)
    nutr_goals = await nutrition_repo.get_nutrition_goals(user_id)
    water_cur = await water_repo.get_daily_water(user_id, local_date)
    water_tgt = await water_repo.get_water_target(user_id)
    today_tasks = await tasks_repo.get_today_tasks(user_id, local_date)
    has_uncompleted = any(not t.get("is_completed") for t in today_tasks)
    text = format_checkin_text(habits, local_date, nutr_summary, nutr_goals, water_cur, water_tgt, tasks=today_tasks)
    kb = get_checkin_dashboard_keyboard(habits, has_uncompleted_tasks=has_uncompleted)
    await target_message.answer(text, reply_markup=kb)


@router.message(F.text == "🌙 Вечерний чек-ин")
@router.message(Command("checkin"))
async def cmd_checkin(
    message: Message,
    local_date: str,
    local_weekday: int,
) -> None:
    """Trigger manual evening check-in dashboard."""
    if is_widget_enabled():
        user = await users_repo.get_user(message.from_user.id)
        offset = user.get("utc_offset", 3) if user else 3
        await send_widget_card(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            view="checkin",
            local_date=local_date,
            local_weekday=local_weekday,
            utc_offset=offset,
            reply_markup=get_main_reply_keyboard(),
        )
        return

    await render_checkin_view(message, message.from_user.id, local_date, local_weekday)


@router.callback_query(F.data.startswith("checkin_complete:"))
async def cb_checkin_complete(
    callback: CallbackQuery,
    local_date: str,
    local_weekday: int,
) -> None:
    """Mark habit as completed from check-in button."""
    habit_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    habit = await habits_repo.get_habit_by_id(habit_id)
    if not habit or habit["user_id"] != user_id:
        await callback.answer("Привычка не найдена.", show_alert=True)
        return

    # Mark as completed
    if habit.get("habit_type") == "checklist":
        cur_reps, tgt_reps, is_completed = await habits_repo.increment_habit_repeat(
            habit_id=habit_id,
            user_id=user_id,
            logged_date=local_date,
        )
        toast_msg = (
            f"«{habit['name']}» ({cur_reps}/{tgt_reps})! 🎉"
            if is_completed
            else f"«{habit['name']}» отмечено (+1)! (Всего {cur_reps}/{tgt_reps})"
        )
    else:
        await habits_repo.set_habit_completed_manual(
            habit_id=habit_id,
            user_id=user_id,
            logged_date=local_date,
            target_minutes=habit["target_minutes"],
        )
        toast_msg = f"«{habit['name']}» отмечена как выполненная! 🎉"

    # Refresh habits list
    habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
    nutr_summary = await nutrition_repo.get_daily_summary(user_id, local_date)
    nutr_goals = await nutrition_repo.get_nutrition_goals(user_id)
    water_cur = await water_repo.get_daily_water(user_id, local_date)
    water_tgt = await water_repo.get_water_target(user_id)
    today_tasks = await tasks_repo.get_today_tasks(user_id, local_date)
    has_uncompleted = any(not t.get("is_completed") for t in today_tasks)
    text = format_checkin_text(habits, local_date, nutr_summary, nutr_goals, water_cur, water_tgt, tasks=today_tasks)
    kb = get_checkin_dashboard_keyboard(habits, has_uncompleted_tasks=has_uncompleted)

    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning("TelegramBadRequest in cb_checkin_complete: %s", e)
    except Exception as e:
        logger.debug("Failed to edit checkin message: %s", e)

    await callback.answer(toast_msg)


@router.callback_query(F.data == "checkin_finish")
async def cb_checkin_finish(callback: CallbackQuery) -> None:
    """Finish check-in review."""
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n"
            "✨ <b>Чек-ин успешно завершен!</b>\n"
            "Вы отлично потрудились сегодня. Отдыхайте и готовьтесь к завтрашним победам! 🌙",
            reply_markup=None,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning("TelegramBadRequest in cb_checkin_finish: %s", e)
    except Exception as e:
        logger.debug("Failed to finish checkin: %s", e)

    await callback.answer("Чек-ин сохранен!")
