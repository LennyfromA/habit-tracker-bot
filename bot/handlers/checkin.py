"""Handler for interactive evening check-in dashboard."""

from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.database import habits_repo, nutrition_repo, water_repo
from bot.keyboards.inline import get_checkin_dashboard_keyboard
from bot.keyboards.reply import get_main_reply_keyboard

router = Router(name="checkin_router")


def format_checkin_text(
    habits: List[Dict[str, Any]],
    local_date: str,
    nutrition_summary: Optional[Dict[str, Any]] = None,
    nutrition_goals: Optional[Dict[str, Any]] = None,
    water_ml: int = 0,
    water_target: int = 2000,
) -> str:
    """Format check-in status message."""
    total_count = len(habits)
    completed_count = sum(1 for h in habits if h.get("is_completed"))
    frozen_count = sum(1 for h in habits if h.get("is_frozen_today"))

    lines = [
        f"🌙 **Вечерний чек-ин продуктивности!**",
        f"📅 Дата: `{local_date}`",
        f"📊 Итог дня: **{completed_count + frozen_count}/{total_count}** выполнено/заморожено\n",
    ]

    for h in habits:
        spent = h.get("spent_minutes", 0)
        target = h["target_minutes"]
        if h.get("is_completed"):
            lines.append(f"✅ «{h['name']}» — {spent}/{target} мин (Выполнено)")
        elif h.get("is_frozen_today"):
            lines.append(f"❄️ «{h['name']}» — Заморожено сегодня")
        else:
            remaining = max(0, target - spent)
            lines.append(f"⏳ «{h['name']}» — {spent}/{target} мин (Осталось: {remaining} мин)")

    if nutrition_summary and nutrition_summary.get("meal_count", 0) > 0:
        cals = nutrition_summary.get("total_calories", 0)
        target = (nutrition_goals or {}).get("target_calories", 2000)
        diff = target - cals
        diff_str = f"осталось {diff} ккал" if diff >= 0 else f"+{abs(diff)} ккал сверх нормы"
        p = nutrition_summary.get("total_protein", 0)
        f = nutrition_summary.get("total_fat", 0)
        c = nutrition_summary.get("total_carbs", 0)
        macros_part = f" ({p}Б / {f}Ж / {c}У)" if (p > 0 or f > 0 or c > 0) else ""
        lines.append(f"\n🥗 **Питание за сегодня:** {cals}/{target} ккал ({diff_str}){macros_part}")

    if water_ml > 0:
        lines.append(f"💧 **Вода за сегодня:** {water_ml}/{water_target} мл")

    incomplete = [h for h in habits if not h.get("is_completed") and not h.get("is_frozen_today")]
    if incomplete:
        lines.append("\nНажмите на привычку, чтобы быстро отметить её выполнение:")
    else:
        lines.append("\n🎉 Все запланированные привычки на сегодня закрыты! Отличный результат!")

    return "\n".join(lines)


@router.message(F.text == "🌙 Вечерний чек-ин")
@router.message(Command("checkin"))
async def cmd_checkin(
    message: Message,
    local_date: str,
    local_weekday: int,
) -> None:
    """Trigger manual evening check-in dashboard."""
    user_id = message.from_user.id
    habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)

    if not habits:
        await message.answer(
            "🌴 На сегодня у вас не было запланированных привычек. Отдыхайте и набирайтесь сил перед завтрашним днем!",
            reply_markup=get_main_reply_keyboard(),
        )
        return

    nutr_summary = await nutrition_repo.get_daily_summary(user_id, local_date)
    nutr_goals = await nutrition_repo.get_nutrition_goals(user_id)
    water_cur = await water_repo.get_daily_water(user_id, local_date)
    water_tgt = await water_repo.get_water_target(user_id)
    text = format_checkin_text(habits, local_date, nutr_summary, nutr_goals, water_cur, water_tgt)
    kb = get_checkin_dashboard_keyboard(habits)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


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
    await habits_repo.set_habit_completed_manual(
        habit_id=habit_id,
        user_id=user_id,
        logged_date=local_date,
        target_minutes=habit["target_minutes"],
    )

    # Refresh habits list
    habits = await habits_repo.get_today_habits_with_status(user_id, local_date, local_weekday)
    nutr_summary = await nutrition_repo.get_daily_summary(user_id, local_date)
    nutr_goals = await nutrition_repo.get_nutrition_goals(user_id)
    water_cur = await water_repo.get_daily_water(user_id, local_date)
    water_tgt = await water_repo.get_water_target(user_id)
    text = format_checkin_text(habits, local_date, nutr_summary, nutr_goals, water_cur, water_tgt)
    kb = get_checkin_dashboard_keyboard(habits)

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        pass

    await callback.answer(f"«{habit['name']}» отмечена как выполненная! 🎉")


@router.callback_query(F.data == "checkin_finish")
async def cb_checkin_finish(callback: CallbackQuery) -> None:
    """Finish check-in review."""
    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n"
            "✨ **Чек-ин успешно завершен!**\n"
            "Вы отлично потрудились сегодня. Отдыхайте и готовьтесь к завтрашним победам! 🌙",
            reply_markup=None,
            parse_mode="Markdown",
        )
    except Exception:
        pass

    await callback.answer("Чек-ин сохранен!")
