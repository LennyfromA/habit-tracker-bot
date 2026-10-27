"""Service for parsing meal input strings, formatting nutrition reports and visual dashboards."""

import re
from typing import Any, Dict, List, Optional, Tuple

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


def render_bar(current: float, target: float, length: int = 8) -> str:
    """Format visual progress bar like [■■■■□□□□]."""
    if target <= 0:
        return "[" + "■" * length + "]"
    ratio = min(1.0, max(0.0, current / target))
    filled = round(ratio * length)
    empty = length - filled
    return f"[{'■' * filled}{'□' * empty}]"


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

    # 4. Pattern: Title + Calories with optional explicit letters (e.g. 'Омлет 320 22б 18ж 4у' or 'Кофе 120')
    m_general = re.search(r"^(.*?)\s+(\d+)(?:\s*(?:ккал|kcal))?(?:\s+(.*))?$", text, re.IGNORECASE)
    if m_general:
        title = m_general.group(1).strip()
        cals = int(m_general.group(2))
        rest = m_general.group(3) or ""

        # Extract protein, fat, carbs if present
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

    return None


def format_meal_item(meal: Dict[str, Any]) -> str:
    """Format single meal row for display."""
    type_label = MEAL_TYPE_MAP.get(meal.get("meal_type", "snack"), "🍏 Перекус")
    title = meal.get("title", "Еда")
    cals = meal.get("calories", 0)
    p = meal.get("protein", 0.0)
    f = meal.get("fat", 0.0)
    c = meal.get("carbs", 0.0)

    # Format time if created_at is present (e.g. "2026-10-27 15:30:00")
    created_at = str(meal.get("created_at") or "")
    time_part = ""
    if len(created_at) >= 16:
        time_part = f"`{created_at[11:16]}` "

    macros_text = ""
    if p > 0 or f > 0 or c > 0:
        macros_text = f" ({round(p, 1)}Б / {round(f, 1)}Ж / {round(c, 1)}У)"

    return f"{time_part}[{type_label}] **{title}** — {cals} ккал{macros_text}"


def format_nutrition_dashboard(
    goals: Dict[str, Any],
    summary: Dict[str, Any],
    meals: List[Dict[str, Any]],
    local_date: str,
    water_current: int = 0,
    water_target: int = 2000,
    today_weight: Optional[float] = None,
) -> str:
    """Build full markdown nutrition dashboard for today."""
    target_cals = goals.get("target_calories", 2000)
    target_p = goals.get("target_protein", 0.0)
    target_f = goals.get("target_fat", 0.0)
    target_c = goals.get("target_carbs", 0.0)

    total_cals = summary.get("total_calories", 0)
    total_p = summary.get("total_protein", 0.0)
    total_f = summary.get("total_fat", 0.0)
    total_c = summary.get("total_carbs", 0.0)

    cals_bar = render_bar(total_cals, target_cals)
    diff_cals = target_cals - total_cals
    if diff_cals >= 0:
        cals_diff_text = f"Осталось: {diff_cals} ккал"
    else:
        cals_diff_text = f"Перебор: +{abs(diff_cals)} ккал ⚠️"

    lines = [
        f"🥗 **Дневник питания и здоровья**",
        f"📅 Дата: `{local_date}`\n",
        f"🔥 **Калории:** `{cals_bar}` **{total_cals}** / {target_cals} ккал ({cals_diff_text})",
    ]

    # Show macros
    if target_p > 0 or target_f > 0 or target_c > 0:
        p_bar = render_bar(total_p, target_p, length=6) if target_p > 0 else "[-]"
        f_bar = render_bar(total_f, target_f, length=6) if target_f > 0 else "[-]"
        c_bar = render_bar(total_c, target_c, length=6) if target_c > 0 else "[-]"
        lines.append(f"🥩 **Белки:**    `{p_bar}` {total_p}/{target_p} г")
        lines.append(f"🥑 **Жиры:**     `{f_bar}` {total_f}/{target_f} г")
        lines.append(f"🍞 **Углеводы:** `{c_bar}` {total_c}/{target_c} г")
    else:
        # Optional macros without strict goals
        lines.append(
            f"📊 **БЖУ за день:** 🥩 {total_p}г белков | 🥑 {total_f}г жиров | 🍞 {total_c}г углеводов"
        )

    # Show water
    if water_target > 0:
        w_bar = render_bar(water_current, water_target)
        w_diff = water_target - water_current
        w_diff_text = f"Осталось: {w_diff} мл" if w_diff > 0 else "Норма выполнена! 🎉"
        lines.append(f"💧 **Вода:**    `{w_bar}` **{water_current}** / {water_target} мл ({w_diff_text})")

    # Show weight if recorded
    if today_weight is not None:
        lines.append(f"⚖️ **Вес:**    **{today_weight} кг** (утром зафиксировано)")

    lines.append("")

    if not meals:
        lines.append("🍽 За сегодня записей еды пока нет.")
        lines.append("Нажмите **«➕ Добавить перекус / еду»** или просто напишите, например:")
        lines.append("• `Кофе с молоком 120`")
        lines.append("• `Творог 180 20/4/3`")
        lines.append("• `+250`")
    else:
        lines.append(f"📋 **Приемы пищи за сегодня ({len(meals)}):**")
        for m in meals:
            lines.append(f"• {format_meal_item(m)}")

    return "\n".join(lines)


def format_weight_dashboard(
    stats: Dict[str, Any],
    local_date: str,
    reminder_enabled: bool,
    reminder_time: str,
) -> str:
    """Format weight dashboard and history."""
    lines = [
        "⚖️ **Трекер утреннего веса**",
        f"📅 Дата: `{local_date}`\n",
    ]
    today_w = stats.get("today_weight")
    latest_w = stats.get("latest_weight")
    if today_w is not None:
        lines.append(f"✅ **Сегодняшний вес:** **{today_w} кг**")
    elif latest_w is not None:
        lines.append(f"⏳ **Сегодня вес еще не записан.**")
        lines.append(f"Последний замер: **{latest_w} кг**")
    else:
        lines.append("⏳ Вы пока еще не записывали свой вес.")

    diff_prev = stats.get("diff_from_previous")
    if diff_prev is not None:
        sign = "+" if diff_prev > 0 else ""
        lines.append(f"📊 Динамика с прошлого замера: **{sign}{diff_prev} кг**")

    diff_week = stats.get("diff_from_week_ago")
    if diff_week is not None:
        sign = "+" if diff_week > 0 else ""
        lines.append(f"📈 За неделю: **{sign}{diff_week} кг**")

    status_str = f"Включено (каждое утро в `{reminder_time}`)" if reminder_enabled else "Выключено"
    lines.append(f"\n⏰ **Утреннее напоминание:** {status_str}")

    history = stats.get("history", [])
    if history:
        lines.append("\n📋 **Последние замеры:**")
        for item in history[:5]:
            note_str = f" — _{item['note']}_" if item.get("note") else ""
            lines.append(f"• `{item['logged_date']}`: **{item['weight']} кг**{note_str}")

    return "\n".join(lines)
