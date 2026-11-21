"""Service for parsing meal input strings, formatting nutrition reports and visual dashboards."""

import html
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bot.database.food_repo import calculate_portion_macros
from bot.utils.formatting import render_progress_bar

# Alias for backward compatibility
render_bar = render_progress_bar

MEAL_TYPE_MAP = {
    "breakfast": "🍳 Завтрак",
    "lunch": "🍲 Обед",
    "dinner": "🍝 Ужин",
    "snack": "🍏 Перекус",
}

MEAL_TYPE_KEYWORDS = {
    "завтрак": "breakfast",
    "обед": "lunch",
    "ужин": "dinner",
    "перекус": "snack",
    "snack": "snack",
    "breakfast": "breakfast",
    "lunch": "lunch",
    "dinner": "dinner",
}


def parse_meal_input(raw: str, default_meal_type: str = "snack") -> Optional[Dict[str, Any]]:
    """
    Parse a user message into structured meal data.
    Supports:
      - 'Кофе с молоком 120'
      - '+250' or '250'
      - 'Яблоко 75 ккал'
      - 'Творог 180 20/4/3'
      - 'Банан 90 1.5/0.2/22'
      - 'Обед: Гречка с курицей 450 35б 8ж 45у'
      - 'Завтрак: Овсянка 300 ккал 10 б 6 ж 50 у'
    Ignores food queries containing units/percentages (e.g. 'Творог 5%', 'Кефир 1%', '2 шт').
    """
    text = raw.strip()
    if not text:
        return None

    meal_type = default_meal_type

    # 1. Check for leading meal type prefix e.g. "Завтрак: ", "Обед - ", "Перекус "
    prefix_match = re.match(r"^([а-яёa-z]+)[:\s\-]+(.*)$", text, re.IGNORECASE)
    if prefix_match:
        word = prefix_match.group(1).lower()
        if word in MEAL_TYPE_KEYWORDS:
            meal_type = MEAL_TYPE_KEYWORDS[word]
            text = prefix_match.group(2).strip()

    # Guard: Reject items ending with percentage, pieces, eggs, grams without calories
    if re.search(r"(?:%|шт|штук\w*|яиц\w*|яйц\w*|г|гр|грамм\w*|ml|мл)\s*$", text, re.IGNORECASE):
        if not re.search(r"\d+\s*(?:ккал|kcal)", text, re.IGNORECASE):
            return None

    # 2. Pattern: just digits like "+250", "250", "250 ккал"
    m_digits = re.match(r"^\+?(\d+)(?:\s*(?:ккал|kcal))?$", text, re.IGNORECASE)
    if m_digits:
        return {
            "title": "Перекус",
            "calories": int(m_digits.group(1)),
            "protein": 0.0,
            "fat": 0.0,
            "carbs": 0.0,
            "meal_type": meal_type,
        }

    # 3. Pattern: Title + Calories + P/F/C with slashes (e.g. 'Творог 180 20/4/3' or 'Банан 90 1.5/0.2/22')
    m_slash = re.search(
        r"^(.*?)\s+(\d+)(?:\s*(?:ккал|kcal))?\s+(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)$",
        text,
        re.IGNORECASE,
    )
    if m_slash:
        title = m_slash.group(1).strip()
        cals = int(m_slash.group(2))
        p = float(m_slash.group(3).replace(",", "."))
        f = float(m_slash.group(4).replace(",", "."))
        c = float(m_slash.group(5).replace(",", "."))
        return {
            "title": title or "Блюдо",
            "calories": cals,
            "protein": round(p, 1),
            "fat": round(f, 1),
            "carbs": round(c, 1),
            "meal_type": meal_type,
        }

    # 4. Pattern: Title + Calories + explicit macro letters (e.g. 'Омлет 320 22б 18ж 4у')
    m_macros = re.search(
        r"^(.*?)\s+(\d+)(?:\s*(?:ккал|kcal))?\s+(\d+.*(?:б|b|ж|f|у|c).*)$",
        text,
        re.IGNORECASE,
    )
    if m_macros:
        title = m_macros.group(1).strip()
        cals = int(m_macros.group(2))
        rest = (m_macros.group(3) or "").strip()

        p_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:б|b|белк\w*|prot\w*)", rest, re.IGNORECASE)
        f_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ж|f|жир\w*|fat\w*)", rest, re.IGNORECASE)
        c_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:у|c|угл\w*|carb\w*)", rest, re.IGNORECASE)

        p = float(p_match.group(1).replace(",", ".")) if p_match else 0.0
        f = float(f_match.group(1).replace(",", ".")) if f_match else 0.0
        c = float(c_match.group(1).replace(",", ".")) if c_match else 0.0

        return {
            "title": title or "Перекус",
            "calories": cals,
            "protein": round(p, 1),
            "fat": round(f, 1),
            "carbs": round(c, 1),
            "meal_type": meal_type,
        }

    # 5. Pattern: Title + Calories at the end (e.g. 'Кофе 120' or 'Творог 5% 150' or 'Овсянка 220')
    m_cals_end = re.search(r"^(.*)\s+(\d+)(?:\s*(?:ккал|kcal))?$", text, re.IGNORECASE)
    if m_cals_end:
        title = m_cals_end.group(1).strip()
        cals = int(m_cals_end.group(2))

        # If title ends with preposition like 'из', 'от', 'с', 'на', it's not calories (e.g. 'Яичница из 2')
        if re.search(r"\b(из|от|в|на|до|с|со|по)\s*$", title, re.IGNORECASE):
            return None

        has_kcal_unit = bool(re.search(r"\d+\s*(?:ккал|kcal)", text, re.IGNORECASE))
        # Small numbers (< 15) without explicit 'ккал' are percentages or quantities (e.g. 'Творог 5')
        if not has_kcal_unit and cals < 15:
            return None

        return {
            "title": title or "Перекус",
            "calories": cals,
            "protein": 0.0,
            "fat": 0.0,
            "carbs": 0.0,
            "meal_type": meal_type,
        }

    return None


def format_product_card(product: Dict[str, Any], show_macros: bool = True) -> str:
    """Format product summary card with 100g macros in HTML."""
    name = html.escape(product["name"])
    cals = round(float(product["calories_100g"]))
    p = float(product.get("protein_100g", 0.0))
    f = float(product.get("fat_100g", 0.0))
    c = float(product.get("carbs_100g", 0.0))
    macros_part = f" ({p}Б / {f}Ж / {c}У)" if show_macros else ""
    return (
        f"🔍 <b>{name}</b>\n\n"
        f"📊 <b>На 100 грамм:</b> <code>{cals} ккал</code>{macros_part}\n\n"
        f"Выберите размер порции или укажите свой вес:"
    )


def format_meal_item(meal: Dict[str, Any], show_macros: bool = True, utc_offset: int = 0) -> str:
    """Format single meal row for display in HTML, converting created_at to local time."""
    type_label = MEAL_TYPE_MAP.get(meal.get("meal_type", "snack"), "🍏 Перекус")
    title = html.escape(meal.get("title", "Еда"))
    cals = meal.get("calories", 0)
    p = meal.get("protein", 0.0)
    f = meal.get("fat", 0.0)
    c = meal.get("carbs", 0.0)

    # Format time if created_at is present (converting from UTC to user's local timezone)
    created_at = str(meal.get("created_at") or "")
    time_part = ""
    if len(created_at) >= 16:
        try:
            utc_dt = datetime.strptime(created_at[:19], "%Y-%m-%d %H:%M:%S")
            local_dt = utc_dt + timedelta(hours=utc_offset)
            time_part = f"<code>{local_dt.strftime('%H:%M')}</code> "
        except Exception:
            time_part = f"<code>{created_at[11:16]}</code> "

    macros_text = ""
    if show_macros and (p > 0 or f > 0 or c > 0):
        macros_text = f" ({round(p, 1)}Б / {round(f, 1)}Ж / {round(c, 1)}У)"

    return f"{time_part}[{type_label}] <b>{title}</b> — {cals} ккал{macros_text}"


def format_nutrition_dashboard(
    goals: Dict[str, Any],
    summary: Dict[str, Any],
    meals: List[Dict[str, Any]],
    local_date: str,
    water_current: int = 0,
    water_target: int = 2000,
    today_weight: Optional[float] = None,
    utc_offset: int = 0,
) -> str:
    """Build full HTML nutrition dashboard for today."""
    target_cals = goals.get("target_calories", 2000)
    target_p = goals.get("target_protein", 0.0)
    target_f = goals.get("target_fat", 0.0)
    target_c = goals.get("target_carbs", 0.0)
    track_bju = bool(goals.get("track_bju", 1))
    track_water = bool(goals.get("track_water", 1))

    total_cals = summary.get("total_calories", 0)
    total_p = summary.get("total_protein", 0.0)
    total_f = summary.get("total_fat", 0.0)
    total_c = summary.get("total_carbs", 0.0)

    cals_bar = render_progress_bar(total_cals, target_cals)
    diff_cals = target_cals - total_cals
    if diff_cals >= 0:
        cals_diff_text = f"Осталось: {diff_cals} ккал"
    else:
        cals_diff_text = f"Перебор: +{abs(diff_cals)} ккал ⚠️"

    lines = [
        f"🥗 <b>Дневник питания и здоровья</b>",
        f"📅 Дата: <code>{local_date}</code>\n",
        f"🔥 <b>Калории:</b> <code>{cals_bar}</code> <b>{total_cals}</b> / {target_cals} ккал ({cals_diff_text})",
    ]

    # Show macros if tracking is enabled
    if track_bju:
        if target_p > 0 or target_f > 0 or target_c > 0:
            p_bar = render_progress_bar(total_p, target_p, length=6) if target_p > 0 else "[-]"
            f_bar = render_progress_bar(total_f, target_f, length=6) if target_f > 0 else "[-]"
            c_bar = render_progress_bar(total_c, target_c, length=6) if target_c > 0 else "[-]"
            lines.append(f"🥩 <b>Белки:</b>    <code>{p_bar}</code> {total_p}/{target_p} г")
            lines.append(f"🥑 <b>Жиры:</b>     <code>{f_bar}</code> {total_f}/{target_f} г")
            lines.append(f"🍞 <b>Углеводы:</b> <code>{c_bar}</code> {total_c}/{target_c} г")
        else:
            # Optional macros without strict goals
            lines.append(
                f"📊 <b>БЖУ за день:</b> 🥩 {total_p}г белков | 🥑 {total_f}г жиров | 🍞 {total_c}г углеводов"
            )

    # Show water if tracking is enabled
    if track_water and water_target > 0:
        w_bar = render_progress_bar(water_current, water_target)
        w_diff = water_target - water_current
        w_diff_text = f"Осталось: {w_diff} мл" if w_diff > 0 else "Норма выполнена! 🎉"
        lines.append(f"💧 <b>Вода:</b>    <code>{w_bar}</code> <b>{water_current}</b> / {water_target} мл ({w_diff_text})")

    # Show weight if recorded
    if today_weight is not None:
        lines.append(f"⚖️ <b>Вес:</b>    <b>{today_weight} кг</b> (утром зафиксировано)")

    lines.append("")

    if not meals:
        lines.append("🍽 За сегодня записей еды пока нет.")
        lines.append("Нажмите <b>«➕ Добавить перекус / еду»</b> или просто напишите, например:")
        lines.append("• <code>Кофе с молоком 120</code>")
        if track_bju:
            lines.append("• <code>Творог 180 20/4/3</code>")
        lines.append("• <code>+250</code>")
    else:
        lines.append(f"📋 <b>Приемы пищи за сегодня ({len(meals)}):</b>")
        for m in meals:
            lines.append(f"• {format_meal_item(m, show_macros=track_bju, utc_offset=utc_offset)}")

    return "\n".join(lines)


def format_weight_dashboard(
    stats: Dict[str, Any],
    local_date: str,
    reminder_enabled: bool,
    reminder_time: str,
) -> str:
    """Format weight dashboard and history in HTML."""
    lines = [
        "⚖️ <b>Трекер утреннего веса</b>",
        f"📅 Дата: <code>{local_date}</code>\n",
    ]
    today_w = stats.get("today_weight")
    latest_w = stats.get("latest_weight")
    if today_w is not None:
        lines.append(f"✅ <b>Сегодняшний вес:</b> <b>{today_w} кг</b>")
    elif latest_w is not None:
        lines.append(f"⏳ <b>Сегодня вес еще не записан.</b>")
        lines.append(f"Последний замер: <b>{latest_w} кг</b>")
    else:
        lines.append("⏳ Вы пока еще не записывали свой вес.")

    diff_prev = stats.get("diff_from_previous")
    if diff_prev is not None:
        sign = "+" if diff_prev > 0 else ""
        lines.append(f"📊 Динамика с прошлого замера: <b>{sign}{diff_prev} кг</b>")

    diff_week = stats.get("diff_from_week_ago")
    if diff_week is not None:
        sign = "+" if diff_week > 0 else ""
        lines.append(f"📈 За неделю: <b>{sign}{diff_week} кг</b>")

    status_str = f"Включено (каждое утро в <code>{reminder_time}</code>)" if reminder_enabled else "Выключено"
    lines.append(f"\n⏰ <b>Утреннее напоминание:</b> {status_str}")

    history = stats.get("history", [])
    if history:
        lines.append("\n📋 <b>Последние замеры:</b>")
        for item in history[:5]:
            note_str = f" — <i>{html.escape(item['note'])}</i>" if item.get("note") else ""
            lines.append(f"• <code>{item['logged_date']}</code>: <b>{item['weight']} кг</b>{note_str}")

    return "\n".join(lines)
