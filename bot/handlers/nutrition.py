"""Handler for nutrition tracking: meals, snacks, water intake, morning weight tracking, and reminders."""

import re
from typing import Any, Dict, Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database import food_repo, nutrition_repo, users_repo, water_repo, weight_repo
from bot.keyboards.inline import (
    get_food_portions_keyboard,
    get_food_search_results_keyboard,
    get_meal_category_inline_keyboard,
    get_meal_input_inline_keyboard,
    get_meals_delete_keyboard,
    get_nutrition_dashboard_keyboard,
    get_weight_dashboard_keyboard,
    get_weight_reminder_time_keyboard,
)
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.nutrition_parser import (
    MEAL_TYPE_MAP,
    format_nutrition_dashboard,
    format_weight_dashboard,
    parse_meal_input,
    render_bar,
)
from bot.states.nutrition_states import NutritionStates

router = Router(name="nutrition_router")


async def build_nutrition_view(user_id: int, local_date: str) -> Tuple[str, Any]:
    """Helper to assemble full nutrition dashboard text and keyboard."""
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    summary = await nutrition_repo.get_daily_summary(user_id, local_date)
    meals = await nutrition_repo.get_meals_for_date(user_id, local_date)
    water_cur = await water_repo.get_daily_water(user_id, local_date)
    water_tgt = await water_repo.get_water_target(user_id)
    weight_rec = await weight_repo.get_weight_for_date(user_id, local_date)
    today_w = weight_rec["weight"] if weight_rec else None

    text = format_nutrition_dashboard(
        goals=goals,
        summary=summary,
        meals=meals,
        local_date=local_date,
        water_current=water_cur,
        water_target=water_tgt,
        today_weight=today_w,
    )
    kb = get_nutrition_dashboard_keyboard(has_meals=bool(meals), has_water=(water_cur > 0))
    return text, kb


async def build_weight_view(user_id: int, local_date: str) -> Tuple[str, Any]:
    """Helper to assemble weight tracking text and keyboard."""
    stats = await weight_repo.get_weight_stats(user_id, local_date)
    user = await users_repo.get_user(user_id)
    rem_enabled = bool(user.get("weight_reminder_enabled", 0)) if user else False
    rem_time = user.get("weight_reminder_time", "08:00") if user else "08:00"

    text = format_weight_dashboard(stats, local_date, rem_enabled, rem_time)
    kb = get_weight_dashboard_keyboard(rem_enabled)
    return text, kb


@router.message(Command("cancel"))
@router.message(F.text.casefold().in_(["/отмена", "отмена"]))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Cancel any active nutrition FSM state."""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer(
            "❌ Действие отменено.",
            reply_markup=get_main_reply_keyboard(),
        )


# =========================================================================
# Nutrition & Meal Handlers
# =========================================================================

@router.message(F.text.in_(["🍏 Здоровье", "🥗 Питание"]))
@router.message(Command("health"))
@router.message(Command("nutrition"))
@router.message(Command("food"))
@router.message(Command("eda"))
async def cmd_nutrition_dashboard(
    message: Message,
    local_date: str,
    state: FSMContext,
) -> None:
    """Display nutrition dashboard for today or handle direct quick argument."""
    await state.clear()
    user_id = message.from_user.id
    text = (message.text or "").strip()

    # Check if command has arguments (e.g. /food Кофе с молоком 120 or /eda 250)
    parts = text.split(maxsplit=1)
    if len(parts) > 1 and parts[0].startswith("/"):
        raw_arg = parts[1]
        parsed = parse_meal_input(raw_arg, default_meal_type="snack")
        if parsed:
            await nutrition_repo.add_meal(
                user_id=user_id,
                logged_date=local_date,
                title=parsed["title"],
                calories=parsed["calories"],
                protein=parsed["protein"],
                fat=parsed["fat"],
                carbs=parsed["carbs"],
                meal_type=parsed["meal_type"],
            )
            dash_text, kb = await build_nutrition_view(user_id, local_date)
            type_name = MEAL_TYPE_MAP.get(parsed["meal_type"], "🍏 Перекус")
            macros_note = ""
            if parsed["protein"] > 0 or parsed["fat"] > 0 or parsed["carbs"] > 0:
                macros_note = f" ({parsed['protein']}Б / {parsed['fat']}Ж / {parsed['carbs']}У)"

            await message.answer(
                f"✅ Записано: [{type_name}] **{parsed['title']}** — {parsed['calories']} ккал{macros_note}!\n\n"
                + dash_text,
                reply_markup=kb,
            )
            return

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(dash_text, reply_markup=kb)


@router.message(Command("snack"))
async def cmd_quick_snack(message: Message, local_date: str) -> None:
    """Quick snack logging shortcut (e.g. /snack Яблоко 80 or /snack 150)."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "ℹ️ Отправьте команду вместе с перекусом, например:\n"
            "• `/snack Кофе с молоком 120`\n"
            "• `/snack Яблоко 70`\n"
            "• `/snack 200`\n"
            "• `/snack Батончик 180 20/4/15`",
        )
        return

    parsed = parse_meal_input(parts[1], default_meal_type="snack")
    if not parsed:
        await message.answer("⚠️ Не удалось распознать калории. Пример: `/snack Кофе с молоком 120`")
        return

    await nutrition_repo.add_meal(
        user_id=user_id,
        logged_date=local_date,
        title=parsed["title"],
        calories=parsed["calories"],
        protein=parsed["protein"],
        fat=parsed["fat"],
        carbs=parsed["carbs"],
        meal_type="snack",
    )

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(
        f"✅ Перекус добавлен: **{parsed['title']}** (+{parsed['calories']} ккал)\n\n" + dash_text,
        reply_markup=kb,
    )


@router.callback_query(F.data == "nutr_refresh")
async def cb_nutr_refresh(callback: CallbackQuery, local_date: str, state: FSMContext) -> None:
    """Refresh nutrition dashboard message."""
    await state.clear()
    user_id = callback.from_user.id
    dash_text, kb = await build_nutrition_view(user_id, local_date)
    try:
        await callback.message.edit_text(dash_text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Обновлено")


def format_product_card(product: Dict[str, Any]) -> str:
    """Format product summary card with 100g macros."""
    name = product["name"]
    cals = round(float(product["calories_100g"]))
    p = float(product.get("protein_100g", 0.0))
    f = float(product.get("fat_100g", 0.0))
    c = float(product.get("carbs_100g", 0.0))
    return (
        f"🔍 **{name}**\n\n"
        f"📊 **На 100 грамм:** `{cals} ккал` ({p}Б / {f}Ж / {c}У)\n\n"
        f"Выберите размер порции или укажите свой вес:"
    )


@router.callback_query(F.data == "nutr_add")
async def cb_nutr_add(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to choose meal category or enter directly, showing frequent meals."""
    user_id = callback.from_user.id
    frequent = await nutrition_repo.get_frequent_meals(user_id, limit=4)
    await state.set_state(NutritionStates.waiting_for_meal_text)
    await state.update_data(meal_type="snack")

    prompt_text = "🍽 **Добавление приема пищи**\n\n"
    if frequent:
        prompt_text += "⚡ **Ваши частые блюда (быстрый ввод в 1 клик):**\n"
        prompt_text += "Либо выберите категорию или напишите название продукта/калории:"
    else:
        prompt_text += (
            "Выберите категорию или отправьте название продукта (например: `банан`, `гречка`, `курица`):\n\n"
            "Либо отправьте сразу с калориями: `Кофе с молоком 120`."
        )

    await callback.message.edit_text(
        prompt_text,
        reply_markup=get_meal_category_inline_keyboard(frequent_meals=frequent),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("nutr_cat:"))
async def cb_nutr_cat(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle meal category selection."""
    meal_type = callback.data.split(":")[1]
    user_id = callback.from_user.id
    await state.set_state(NutritionStates.waiting_for_meal_text)
    await state.update_data(meal_type=meal_type)

    type_name = MEAL_TYPE_MAP.get(meal_type, "Прием пищи")
    frequent = await nutrition_repo.get_frequent_meals(user_id, meal_type=meal_type, limit=4)

    prompt_lines = [
        f"✍️ Категория: **{type_name}**\n",
    ]
    if frequent:
        prompt_lines.append("⚡ **Ваши частые блюда в этой категории:**\n")
    prompt_lines.append(
        "Напишите название продукта (например: `банан`, `овсянка`, `яйцо`),\n"
        "или укажите свои калории (например: `Творог 180 20/4/3` или `250`).\n\n"
        "_(Отправьте /cancel для отмены)_"
    )

    await callback.message.edit_text(
        "\n".join(prompt_lines),
        reply_markup=get_meal_input_inline_keyboard(frequent_meals=frequent),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("meal_repeat:"))
async def cb_meal_repeat(
    callback: CallbackQuery,
    local_date: str,
    state: FSMContext,
) -> None:
    """Instantly log a frequent meal in 1 click."""
    meal_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    meal = await nutrition_repo.get_meal_by_id(meal_id, user_id)
    if not meal:
        await callback.answer("Блюдо не найдено.", show_alert=True)
        return

    data = await state.get_data()
    chosen_type = data.get("meal_type") or meal.get("meal_type") or "snack"
    await state.clear()

    added = await nutrition_repo.add_meal(
        user_id=user_id,
        logged_date=local_date,
        title=meal["title"],
        calories=meal["calories"],
        protein=meal["protein"],
        fat=meal["fat"],
        carbs=meal["carbs"],
        meal_type=chosen_type,
    )

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    type_name = MEAL_TYPE_MAP.get(chosen_type, "🍏 Перекус")
    await callback.message.edit_text(
        f"✅ Добавлено в 1 клик: [{type_name}] **{added['title']}** (+{added['calories']} ккал)!\n\n" + dash_text,
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await callback.answer(f"«{added['title']}» (+{added['calories']} ккал) записано!")


@router.message(NutritionStates.waiting_for_meal_text)
async def process_meal_input(message: Message, state: FSMContext, local_date: str) -> None:
    """Process user food entry in FSM: manual calories or product search."""
    user_id = message.from_user.id
    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        return

    data = await state.get_data()
    chosen_type = data.get("meal_type", "snack")

    # 1. Try manual entry parser first (if user explicitly provided numbers)
    parsed = parse_meal_input(raw_text, default_meal_type=chosen_type)
    if parsed:
        await state.clear()
        await nutrition_repo.add_meal(
            user_id=user_id,
            logged_date=local_date,
            title=parsed["title"],
            calories=parsed["calories"],
            protein=parsed["protein"],
            fat=parsed["fat"],
            carbs=parsed["carbs"],
            meal_type=parsed["meal_type"],
        )
        type_name = MEAL_TYPE_MAP.get(parsed["meal_type"], "🍏 Перекус")
        macros_info = ""
        if parsed["protein"] > 0 or parsed["fat"] > 0 or parsed["carbs"] > 0:
            macros_info = f" ({parsed['protein']}Б / {parsed['fat']}Ж / {parsed['carbs']}У)"

        dash_text, kb = await build_nutrition_view(user_id, local_date)
        await message.answer(
            f"✅ Добавлено: [{type_name}] **{parsed['title']}** — {parsed['calories']} ккал{macros_info}!\n\n"
            + dash_text,
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return

    # 2. If no calories provided, search local food database
    matches = await food_repo.search_food(raw_text, limit=5)
    if not matches:
        await message.answer(
            f"🔍 По запросу «**{raw_text}**» продуктов в базе не найдено.\n\n"
            f"Вы можете записать блюдо вручную с калориями, например:\n"
            f"• `{raw_text} 250`\n"
            f"• `{raw_text} 180 20/4/3` _(ккал Б/Ж/У)_\n\n"
            f"_(Или отправьте /cancel для отмены)_",
            parse_mode="Markdown",
        )
        return

    if len(matches) == 1:
        # Exactly 1 match found -> show portions selector immediately
        prod = matches[0]
        card_text = format_product_card(prod)
        await message.answer(
            card_text,
            reply_markup=get_food_portions_keyboard(prod),
            parse_mode="Markdown",
        )
        return

    # Multiple matches found -> show list to choose from
    await message.answer(
        f"🔍 Найдено несколько вариантов по запросу «**{raw_text}**»:\n"
        f"Выберите подходящий продукт:",
        reply_markup=get_food_search_results_keyboard(matches),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("food_pick:"))
async def cb_food_pick(callback: CallbackQuery) -> None:
    """User picked one product from multiple search results."""
    food_id = int(callback.data.split(":")[1])
    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return

    card_text = format_product_card(product)
    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_food_portions_keyboard(product),
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("food_portion:"))
async def cb_food_portion(
    callback: CallbackQuery,
    local_date: str,
    state: FSMContext,
) -> None:
    """User selected a preset portion of a catalog food."""
    parts = callback.data.split(":")
    food_id = int(parts[1])
    portion_idx = int(parts[2])

    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return

    portions = product.get("portions", [])
    if portion_idx < 0 or portion_idx >= len(portions):
        await callback.answer("Порция не найдена.", show_alert=True)
        return

    selected_portion = portions[portion_idx]
    grams = float(selected_portion.get("grams", 100))
    macros = food_repo.calculate_portion_macros(product, grams)

    data = await state.get_data()
    chosen_type = data.get("meal_type", "snack")
    await state.clear()

    portion_name = selected_portion.get("name", f"{int(grams)}г")
    title = f"{product['name']} ({portion_name})"

    user_id = callback.from_user.id
    await nutrition_repo.add_meal(
        user_id=user_id,
        logged_date=local_date,
        title=title,
        calories=macros["calories"],
        protein=macros["protein"],
        fat=macros["fat"],
        carbs=macros["carbs"],
        meal_type=chosen_type,
    )

    type_name = MEAL_TYPE_MAP.get(chosen_type, "🍏 Перекус")
    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await callback.message.edit_text(
        f"✅ Добавлено: [{type_name}] **{title}** — {macros['calories']} ккал "
        f"({macros['protein']}Б / {macros['fat']}Ж / {macros['carbs']}У)!\n\n" + dash_text,
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await callback.answer(f"«{product['name']}» (+{macros['calories']} ккал) добавлено!")


@router.callback_query(F.data.startswith("food_custom_g:"))
async def cb_food_custom_g(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type custom grams for selected product."""
    food_id = int(callback.data.split(":")[1])
    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return

    await state.set_state(NutritionStates.waiting_for_product_grams)
    await state.update_data(chosen_food_id=food_id)
    await callback.message.edit_text(
        f"✍️ **Введите вес в граммах для «{product['name']}»:**\n\n"
        f"_(Например: `140` или `250`, отправьте /cancel для отмены)_",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(NutritionStates.waiting_for_product_grams)
async def process_custom_product_grams(
    message: Message,
    state: FSMContext,
    local_date: str,
) -> None:
    """Handle custom grams text entry."""
    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        return

    match = re.search(r"\d+", raw_text)
    if not match or int(match.group(0)) <= 0 or int(match.group(0)) > 5000:
        await message.answer("⚠️ Пожалуйста, введите положительное число грамм от 1 до 5000 (или /cancel):")
        return

    grams = float(match.group(0))
    data = await state.get_data()
    food_id = data.get("chosen_food_id")
    chosen_type = data.get("meal_type", "snack")
    await state.clear()

    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await message.answer("⚠️ Продукт не найден.", reply_markup=get_main_reply_keyboard())
        return

    macros = food_repo.calculate_portion_macros(product, grams)
    user_id = message.from_user.id
    title = f"{product['name']} ({int(grams)}г)"

    await nutrition_repo.add_meal(
        user_id=user_id,
        logged_date=local_date,
        title=title,
        calories=macros["calories"],
        protein=macros["protein"],
        fat=macros["fat"],
        carbs=macros["carbs"],
        meal_type=chosen_type,
    )

    type_name = MEAL_TYPE_MAP.get(chosen_type, "🍏 Перекус")
    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(
        f"✅ Добавлено: [{type_name}] **{title}** — {macros['calories']} ккал "
        f"({macros['protein']}Б / {macros['fat']}Ж / {macros['carbs']}У)!\n\n" + dash_text,
        reply_markup=kb,
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "nutr_delete_menu")
async def cb_nutr_delete_menu(callback: CallbackQuery, local_date: str) -> None:
    """Show list of meals to delete."""
    user_id = callback.from_user.id
    meals = await nutrition_repo.get_meals_for_date(user_id, local_date)
    if not meals:
        await callback.answer("За сегодня еще нет записей для удаления.", show_alert=True)
        return

    await callback.message.edit_text(
        "🗑 **Выберите запись, которую хотите удалить:**",
        reply_markup=get_meals_delete_keyboard(meals),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("nutr_del:"))
async def cb_nutr_del_item(callback: CallbackQuery, local_date: str) -> None:
    """Delete a specific meal by ID."""
    user_id = callback.from_user.id
    meal_id = int(callback.data.split(":")[1])

    meal = await nutrition_repo.get_meal_by_id(meal_id, user_id)
    meal_name = meal.get("title", "Запись") if meal else "Запись"

    deleted = await nutrition_repo.delete_meal(meal_id, user_id)
    if deleted:
        await callback.answer(f"Удалено: {meal_name}")
    else:
        await callback.answer("Запись не найдена или уже удалена.")

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    try:
        await callback.message.edit_text(dash_text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "nutr_set_goal")
async def cb_nutr_set_goal(callback: CallbackQuery, state: FSMContext) -> None:
    """Start setting daily calorie target."""
    user_id = callback.from_user.id
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    current_cals = goals.get("target_calories", 2000)

    await callback.message.edit_text(
        f"🎯 **Настройка суточной нормы калорий**\n\n"
        f"Текущая цель: **{current_cals} ккал**.\n\n"
        f"Введите желаемую суточную норму калорий (например: `2100`):\n"
        f"_(Или отправьте /cancel для отмены)_"
    )
    await state.set_state(NutritionStates.waiting_for_calories_goal)
    await callback.answer()


@router.message(NutritionStates.waiting_for_calories_goal)
async def process_calorie_goal(message: Message, state: FSMContext) -> None:
    """Validate calorie goal and optionally ask for BJU."""
    text = (message.text or "").strip()
    match = re.search(r"\d+", text)
    if not match:
        await message.answer("⚠️ Введите число (например: `2000`) или отправьте /cancel.")
        return

    cals = int(match.group(0))
    if cals < 500 or cals > 10000:
        await message.answer("⚠️ Пожалуйста, введите реалистичную норму калорий (от 500 до 10000 ккал).")
        return

    await state.update_data(target_calories=cals)
    await state.set_state(NutritionStates.waiting_for_bju_goal)

    await message.answer(
        f"✅ Цель по калориям: **{cals} ккал**!\n\n"
        f"Хотите также задать суточную норму **БЖУ (в граммах)**?\n"
        f"Отправьте три числа через пробел (Белки Жиры Углеводы), например:\n"
        f"`130 65 250`\n\n"
        f"Или отправьте **/skip**, чтобы оставить БЖУ необязательными."
    )


@router.message(NutritionStates.waiting_for_bju_goal)
async def process_bju_goal(message: Message, state: FSMContext, local_date: str) -> None:
    """Save calorie & BJU goals."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    data = await state.get_data()
    cals = data.get("target_calories", 2000)

    p, f, c = 0.0, 0.0, 0.0
    if text.lower() not in ("/skip", "skip", "пропустить", "-"):
        nums = re.findall(r"\d+(?:[.,]\d+)?", text)
        if len(nums) >= 3:
            p = float(nums[0].replace(",", "."))
            f = float(nums[1].replace(",", "."))
            c = float(nums[2].replace(",", "."))
        else:
            await message.answer(
                "⚠️ Введите 3 числа (Белки Жиры Углеводы), например: `130 65 250`,\n"
                "или отправьте **/skip**, чтобы оставить БЖУ свободными."
            )
            return

    await state.clear()
    await nutrition_repo.set_nutrition_goals(
        user_id=user_id,
        calories=cals,
        protein=p,
        fat=f,
        carbs=c,
    )

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    macros_msg = f" (Б: {p}г, Ж: {f}г, У: {c}г)" if p > 0 else " (без ограничений по БЖУ)"
    await message.answer(
        f"🎉 **Ваша норма успешно сохранена!**\n"
        f"🎯 {cals} ккал{macros_msg}\n\n"
        + dash_text,
        reply_markup=kb,
    )


# =========================================================================
# Water Tracking Handlers
# =========================================================================

@router.callback_query(F.data.startswith("water_add:"))
async def cb_water_add(callback: CallbackQuery, local_date: str) -> None:
    """Add preset amount of water (e.g. 250 ml or 500 ml)."""
    user_id = callback.from_user.id
    amount_ml = int(callback.data.split(":")[1])

    new_total = await water_repo.add_water(user_id, local_date, amount_ml)
    target_ml = await water_repo.get_water_target(user_id)

    await callback.answer(f"💧 +{amount_ml} мл! Всего сегодня: {new_total}/{target_ml} мл")

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    try:
        await callback.message.edit_text(dash_text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "water_undo")
async def cb_water_undo(callback: CallbackQuery, local_date: str) -> None:
    """Undo the last water logging entry for today."""
    user_id = callback.from_user.id
    removed = await water_repo.undo_last_water(user_id, local_date)

    if removed is not None:
        await callback.answer(f"↩️ Отменено: -{removed} мл воды")
    else:
        await callback.answer("За сегодня еще не было записей воды.")

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    try:
        await callback.message.edit_text(dash_text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "water_custom")
async def cb_water_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to enter custom amount of water."""
    await callback.message.edit_text(
        "💧 **Добавление воды**\n\n"
        "Введите количество выпитой воды в миллилитрах (например: `350` или `600`):\n"
        "_(Отправьте /cancel для отмены)_"
    )
    await state.set_state(NutritionStates.waiting_for_water_amount)
    await callback.answer()


@router.message(NutritionStates.waiting_for_water_amount)
async def process_custom_water(message: Message, state: FSMContext, local_date: str) -> None:
    """Process custom water milliliters."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    match = re.search(r"\d+", text)

    if not match:
        await message.answer("⚠️ Введите число в миллилитрах (например: `300`) или отправьте /cancel.")
        return

    amount_ml = int(match.group(0))
    if amount_ml <= 0 or amount_ml > 5000:
        await message.answer("⚠️ Пожалуйста, введите объем от 1 до 5000 мл.")
        return

    await state.clear()
    new_total = await water_repo.add_water(user_id, local_date, amount_ml)
    target_ml = await water_repo.get_water_target(user_id)

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(
        f"💧 Добавлено: **+{amount_ml} мл** воды! (Всего: {new_total}/{target_ml} мл)\n\n"
        + dash_text,
        reply_markup=kb,
    )


@router.message(Command("water"))
@router.message(Command("вода"))
async def cmd_quick_water(message: Message, local_date: str) -> None:
    """Quick water command e.g. /water 350 or /вода 500."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    match = re.search(r"\d+", text)

    if not match:
        target_ml = await water_repo.get_water_target(user_id)
        cur_ml = await water_repo.get_daily_water(user_id, local_date)
        await message.answer(
            f"💧 **Трекер воды**\n"
            f"Сегодня выпито: **{cur_ml}** / {target_ml} мл\n\n"
            f"Чтобы добавить воду быстро, отправьте, например: `/water 250` или нажмите кнопку в меню «🥗 Питание»."
        )
        return

    amount_ml = int(match.group(0))
    new_total = await water_repo.add_water(user_id, local_date, amount_ml)
    target_ml = await water_repo.get_water_target(user_id)

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(
        f"💧 Добавлено: **+{amount_ml} мл** воды! (Сегодня: {new_total}/{target_ml} мл)\n\n"
        + dash_text,
        reply_markup=kb,
    )


# =========================================================================
# Morning Weight Tracking & Reminder Handlers
# =========================================================================

@router.message(Command("weight"))
@router.message(Command("вес"))
async def cmd_weight(message: Message, local_date: str, state: FSMContext) -> None:
    """View weight tracking dashboard or handle direct quick argument like /weight 72.4."""
    await state.clear()
    user_id = message.from_user.id
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    # Check for direct weight argument e.g. /weight 72.5
    if len(parts) > 1:
        match = re.search(r"\d+(?:[.,]\d+)?", parts[1])
        if match:
            w_val = float(match.group(0).replace(",", "."))
            if 20.0 <= w_val <= 300.0:
                await weight_repo.log_weight(user_id, local_date, w_val)
                w_text, kb = await build_weight_view(user_id, local_date)
                await message.answer(f"✅ Вес записан: **{w_val} кг**!\n\n" + w_text, reply_markup=kb)
                return

    w_text, kb = await build_weight_view(user_id, local_date)
    await message.answer(w_text, reply_markup=kb)


@router.callback_query(F.data == "weight_menu")
async def cb_weight_menu(callback: CallbackQuery, local_date: str, state: FSMContext) -> None:
    """Open weight dashboard from inline buttons."""
    await state.clear()
    user_id = callback.from_user.id
    w_text, kb = await build_weight_view(user_id, local_date)
    try:
        await callback.message.edit_text(w_text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "weight_enter")
async def cb_weight_enter(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type their weight."""
    await callback.message.edit_text(
        "⚖️ **Запись утреннего веса**\n\n"
        "Отправьте ваш текущий вес в килограммах (например: `72.4` или `73`):\n"
        "_(Отправьте /cancel для отмены)_"
    )
    await state.set_state(NutritionStates.waiting_for_weight_input)
    await callback.answer()


@router.message(NutritionStates.waiting_for_weight_input)
async def process_weight_input(message: Message, state: FSMContext, local_date: str) -> None:
    """Process user weight measurement."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    match = re.search(r"\d+(?:[.,]\d+)?", text)

    if not match:
        await message.answer("⚠️ Введите число (например: `72.5`) или отправьте /cancel.")
        return

    w_val = float(match.group(0).replace(",", "."))
    if w_val < 20.0 or w_val > 300.0:
        await message.answer("⚠️ Пожалуйста, введите реалистичный вес (от 20 до 300 кг).")
        return

    await state.clear()
    await weight_repo.log_weight(user_id, local_date, w_val)

    w_text, kb = await build_weight_view(user_id, local_date)
    await message.answer(f"✅ Вес записан: **{w_val} кг**!\n\n" + w_text, reply_markup=kb)


@router.callback_query(F.data == "weight_toggle_reminder")
async def cb_weight_toggle_reminder(callback: CallbackQuery, local_date: str) -> None:
    """Toggle weight morning reminder ON/OFF."""
    user_id = callback.from_user.id
    user = await users_repo.get_user(user_id)
    cur_enabled = bool(user.get("weight_reminder_enabled", 0)) if user else False
    new_enabled = 0 if cur_enabled else 1

    await users_repo.update_weight_reminder(user_id, new_enabled)
    status_msg = "Утреннее напоминание ВКЛЮЧЕНО 🔔" if new_enabled else "Утреннее напоминание ВЫКЛЮЧЕНО 🔕"
    await callback.answer(status_msg, show_alert=True)

    w_text, kb = await build_weight_view(user_id, local_date)
    try:
        await callback.message.edit_text(w_text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "weight_choose_time")
async def cb_weight_choose_time(callback: CallbackQuery) -> None:
    """Show preset times for morning weight notification."""
    await callback.message.edit_text(
        "⏰ **Во сколько присылать утреннее напоминание о взвешивании?**\n\n"
        "Выберите удобное время или введите своё вручную:",
        reply_markup=get_weight_reminder_time_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("weight_set_time:"))
async def cb_weight_set_time(callback: CallbackQuery, local_date: str) -> None:
    """Set weight reminder time from presets."""
    user_id = callback.from_user.id
    chosen_time = callback.data.split(":", 1)[1]

    # Automatically enable reminder when time is picked
    await users_repo.update_weight_reminder(user_id, enabled=1, reminder_time=chosen_time)
    await callback.answer(f"Время установлено: {chosen_time} (напоминание включено) ✅", show_alert=True)

    w_text, kb = await build_weight_view(user_id, local_date)
    try:
        await callback.message.edit_text(w_text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "weight_custom_time")
async def cb_weight_custom_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type custom reminder time."""
    await callback.message.edit_text(
        "✍️ **Введите желаемое время утреннего напоминания**\n\n"
        "Формат: `ЧЧ:ММ` (например: `07:15`, `08:45` или `09:00`):\n"
        "_(Отправьте /cancel для отмены)_"
    )
    await state.set_state(NutritionStates.waiting_for_weight_reminder_time)
    await callback.answer()


@router.message(NutritionStates.waiting_for_weight_reminder_time)
async def process_custom_weight_time(message: Message, state: FSMContext, local_date: str) -> None:
    """Validate and save custom morning reminder time."""
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    match = re.match(r"^([0-1]?[0-9]|2[0-3])[:. -]([0-5][0-9])$", raw)

    if not match:
        await message.answer(
            "⚠️ Неверный формат времени. Введите время в формате `ЧЧ:ММ`, например: `07:45`.\n"
            "Или отправьте /cancel."
        )
        return

    hours = int(match.group(1))
    minutes = int(match.group(2))
    time_str = f"{hours:02d}:{minutes:02d}"

    await state.clear()
    await users_repo.update_weight_reminder(user_id, enabled=1, reminder_time=time_str)

    w_text, kb = await build_weight_view(user_id, local_date)
    await message.answer(
        f"✅ Время утреннего взвешивания установлено на **{time_str}** (напоминание включено)!\n\n"
        + w_text,
        reply_markup=kb,
    )
