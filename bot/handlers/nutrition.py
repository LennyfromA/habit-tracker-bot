"""Handler for nutrition tracking: meals, snacks, water intake, morning weight tracking, and reminders."""

import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.database import food_repo, nutrition_repo, users_repo, water_repo, weight_repo
from bot.keyboards.inline import (
    get_food_portions_keyboard,
    get_food_search_results_keyboard,
    get_meal_category_inline_keyboard,
    get_meal_input_inline_keyboard,
    get_meals_delete_keyboard,
    get_nutrition_dashboard_keyboard,
    get_nutrition_goals_menu_keyboard,
    get_weight_dashboard_keyboard,
    get_weight_reminder_time_keyboard,
)
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.nutrition_parser import (
    MEAL_TYPE_MAP,
    format_nutrition_dashboard,
    format_product_card,
    format_weight_dashboard,
    parse_meal_input,
)
from bot.services.widget_launcher import is_widget_enabled, send_widget_card
from bot.states.nutrition_states import NutritionStates

logger = logging.getLogger(__name__)
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
    track_water = bool(goals.get("track_water", 1))

    user = await users_repo.get_user(user_id)
    utc_offset = user.get("utc_offset", 3) if user else 3

    text = format_nutrition_dashboard(
        goals=goals,
        summary=summary,
        meals=meals,
        local_date=local_date,
        water_current=water_cur,
        water_target=water_tgt,
        today_weight=today_w,
        utc_offset=utc_offset,
    )
    kb = get_nutrition_dashboard_keyboard(
        has_meals=bool(meals),
        has_water=(water_cur > 0),
        track_water=track_water,
    )
    return text, kb


async def build_nutrition_goals_view(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Assemble text and keyboard for nutrition goals settings menu (DRY helper)."""
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    cals = goals.get("target_calories", 2000)
    p = goals.get("target_protein", 0.0)
    f = goals.get("target_fat", 0.0)
    c = goals.get("target_carbs", 0.0)
    track_bju = bool(goals.get("track_bju", 1))
    track_water = bool(goals.get("track_water", 1))

    bju_text = f"{p}Б / {f}Ж / {c}У" if (p > 0 or f > 0 or c > 0) else "не задано"

    text = (
        f"🎯 <b>Настройки целей и отслеживания</b>\n\n"
        f"🔥 <b>Цель по калориям:</b> <code>{cals} ккал</code>\n"
        f"📊 <b>Отслеживание БЖУ:</b> {'Включено ✅' if track_bju else 'Выключено ❌'} (норма: <code>{bju_text}</code>)\n"
        f"💧 <b>Трекер воды:</b> {'Включен ✅' if track_water else 'Выключен ❌'}\n\n"
        f"Здесь вы можете изменить суточную калорийность или в 1 клик отключить учет воды или БЖУ, "
        f"чтобы не перегружать экран лишней информацией."
    )
    kb = get_nutrition_goals_menu_keyboard(track_bju=track_bju, track_water=track_water)
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
            if is_widget_enabled():
                user = await users_repo.get_user(user_id)
                offset = user.get("utc_offset", 3) if user else 3
                await send_widget_card(
                    bot=message.bot,
                    chat_id=message.chat.id,
                    user_id=user_id,
                    view="health",
                    local_date=local_date,
                    utc_offset=offset,
                    reply_markup=get_main_reply_keyboard(),
                )
                return

            dash_text, kb = await build_nutrition_view(user_id, local_date)
            esc_title = html.escape(parsed["title"])
            await message.answer(
                f"✅ Быстро добавлено: <b>{esc_title}</b> (+{parsed['calories']} ккал)!\n\n" + dash_text,
                reply_markup=kb,
            )
            return

    if is_widget_enabled():
        user = await users_repo.get_user(user_id)
        offset = user.get("utc_offset", 3) if user else 3
        await send_widget_card(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=user_id,
            view="health",
            local_date=local_date,
            utc_offset=offset,
            reply_markup=get_main_reply_keyboard(),
        )
        return

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(dash_text, reply_markup=kb)


@router.callback_query(F.data == "nutr_refresh")
async def cb_nutr_refresh(callback: CallbackQuery, local_date: str, state: FSMContext) -> None:
    """Refresh nutrition dashboard view."""
    await state.clear()
    user_id = callback.from_user.id
    dash_text, kb = await build_nutrition_view(user_id, local_date)
    try:
        await callback.message.edit_text(dash_text, reply_markup=kb)
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_nutr_refresh: %s", e)
    await callback.answer("Обновлено")


@router.callback_query(F.data == "nutr_add_entry")
async def cb_nutr_add_entry(callback: CallbackQuery, state: FSMContext) -> None:
    """Choose meal category (breakfast, lunch, dinner, snack)."""
    await state.clear()
    text = (
        "🍽 <b>Выберите прием пищи:</b>\n\n"
        "Куда записать блюдо или продукт?"
    )
    await callback.message.edit_text(text, reply_markup=get_meal_category_inline_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("meal_type:"))
async def cb_select_meal_type(callback: CallbackQuery, state: FSMContext) -> None:
    """Show quick favorites or prompt for food entry for chosen meal type."""
    meal_type = callback.data.split(":")[1]
    await state.update_data(meal_type=meal_type)
    user_id = callback.from_user.id

    type_name = MEAL_TYPE_MAP.get(meal_type, "🍏 Перекус")
    recent_meals = await nutrition_repo.get_recent_meals_by_type(user_id, meal_type, limit=4)

    text = (
        f"➕ <b>Добавить: {type_name}</b>\n\n"
        "Напишите название блюда или продукта.\n"
        "Вы можете указать калории вручную или найти продукт в базе:\n\n"
        "• <code>Банан</code> или <code>Творог 5%</code> — поиск по базе с порциями\n"
        "• <code>Кофе с круассаном 340</code> — ручной ввод с калориями\n"
        "• <code>Грудка 180 30/3/0</code> — с калориями и БЖУ (Б/Ж/У)\n\n"
        "<i>(Отправьте /cancel для отмены)</i>"
    )

    kb = get_meal_input_inline_keyboard(recent_meals=recent_meals)
    await state.set_state(NutritionStates.waiting_for_meal_text)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("quick_meal:"))
async def cb_quick_meal(
    callback: CallbackQuery,
    local_date: str,
    state: FSMContext,
) -> None:
    """Quick 1-click addition of a favorite/recent meal."""
    parts = callback.data.split(":")
    meal_id = int(parts[1])
    user_id = callback.from_user.id

    data = await state.get_data()
    chosen_type = data.get("meal_type", "snack")
    await state.clear()

    added = await nutrition_repo.repeat_meal(meal_id, user_id, local_date, target_meal_type=chosen_type)
    if not added:
        await callback.answer("Блюдо не найдено в недавних.", show_alert=True)
        return

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    type_name = MEAL_TYPE_MAP.get(chosen_type, "🍏 Перекус")
    esc_title = html.escape(added["title"])
    try:
        await callback.message.edit_text(
            f"✅ Добавлено в 1 клик: [{type_name}] <b>{esc_title}</b> (+{added['calories']} ккал)!\n\n" + dash_text,
            reply_markup=kb,
        )
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_quick_meal: %s", e)
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

    # Check for exact or strong catalog matches first (e.g. Творог 5%, Кефир 1%, Молоко 2.5%, Яйцо)
    catalog_matches = await food_repo.search_food(raw_text, limit=5)
    has_exact_catalog = bool(
        catalog_matches
        and catalog_matches[0]["name"].casefold() == raw_text.casefold()
    )

    # 1. Try manual entry parser if there is NO exact catalog match
    parsed = None
    if not has_exact_catalog:
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
        goals = await nutrition_repo.get_nutrition_goals(user_id)
        track_bju = bool(goals.get("track_bju", 1))
        macros_info = ""
        if track_bju and (parsed["protein"] > 0 or parsed["fat"] > 0 or parsed["carbs"] > 0):
            macros_info = f" ({parsed['protein']}Б / {parsed['fat']}Ж / {parsed['carbs']}У)"

        dash_text, kb = await build_nutrition_view(user_id, local_date)
        esc_title = html.escape(parsed["title"])
        await message.answer(
            f"✅ Добавлено: [{type_name}] <b>{esc_title}</b> — {parsed['calories']} ккал{macros_info}!\n\n"
            + dash_text,
            reply_markup=kb,
        )
        return

    # 2. If no calories provided or matched catalog, search local food database
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    track_bju = bool(goals.get("track_bju", 1))

    matches = catalog_matches if catalog_matches is not None else await food_repo.search_food(raw_text, limit=5)
    esc_raw = html.escape(raw_text)
    if not matches:
        await message.answer(
            f"🔍 По запросу «<b>{esc_raw}</b>» продуктов в базе не найдено.\n\n"
            f"Вы можете записать блюдо вручную с калориями, например:\n"
            f"• <code>{esc_raw} 250</code>\n"
            f"• <code>{esc_raw} 180 20/4/3</code> <i>(ккал Б/Ж/У)</i>\n\n"
            f"<i>(Или отправьте /cancel для отмены)</i>",
        )
        return

    if len(matches) == 1:
        # Exactly 1 match found -> show portions selector immediately
        prod = matches[0]
        card_text = format_product_card(prod, show_macros=track_bju)
        await message.answer(
            card_text,
            reply_markup=get_food_portions_keyboard(prod),
        )
        return

    # Multiple matches found -> show list to choose from
    await message.answer(
        f"🔍 Найдено несколько вариантов по запросу «<b>{esc_raw}</b>»:\n"
        f"Выберите подходящий продукт:",
        reply_markup=get_food_search_results_keyboard(matches),
    )


@router.callback_query(F.data.startswith("food_pick:"))
async def cb_food_pick(callback: CallbackQuery) -> None:
    """User picked one product from multiple search results."""
    user_id = callback.from_user.id
    food_id = int(callback.data.split(":")[1])
    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return

    goals = await nutrition_repo.get_nutrition_goals(user_id)
    track_bju = bool(goals.get("track_bju", 1))
    card_text = format_product_card(product, show_macros=track_bju)
    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=get_food_portions_keyboard(product),
        )
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_food_pick: %s", e)
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
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    track_bju = bool(goals.get("track_bju", 1))
    macros_info = ""
    if track_bju:
        macros_info = f" ({macros['protein']}Б / {macros['fat']}Ж / {macros['carbs']}У)"

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    esc_title = html.escape(title)
    try:
        await callback.message.edit_text(
            f"✅ Добавлено: [{type_name}] <b>{esc_title}</b> — {macros['calories']} ккал{macros_info}!\n\n"
            + dash_text,
            reply_markup=kb,
        )
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_food_portion: %s", e)
    await callback.answer(f"«{title}» добавлено!")


@router.callback_query(F.data.startswith("food_grams:"))
async def cb_food_grams(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type exact grams for catalog product."""
    food_id = int(callback.data.split(":")[1])
    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await callback.answer("Продукт не найден.", show_alert=True)
        return

    await state.update_data(chosen_food_id=food_id)
    await state.set_state(NutritionStates.waiting_for_product_grams)

    esc_pname = html.escape(product["name"])
    await callback.message.edit_text(
        f"⚖️ <b>«{esc_pname}»</b>\n\n"
        f"Введите вес порции в граммах (число от 1 до 5000):\n"
        f"<i>(Например: <code>140</code> или <code>250</code>, отправьте /cancel для отмены)</i>",
    )
    await callback.answer()


@router.message(NutritionStates.waiting_for_product_grams)
async def process_custom_grams(message: Message, state: FSMContext, local_date: str) -> None:
    """Process user input of exact grams."""
    raw_text = (message.text or "").strip()
    match = re.search(r"\d+", raw_text)
    if not match:
        await message.answer("⚠️ Пожалуйста, введите положительное число грамм от 1 до 5000 (или /cancel):")
        return

    grams = float(match.group(0))
    if grams <= 0 or grams > 5000:
        await message.answer("⚠️ Пожалуйста, введите корректный вес от 1 до 5000 грамм:")
        return

    data = await state.get_data()
    food_id = data.get("chosen_food_id")
    chosen_type = data.get("meal_type", "snack")
    await state.clear()

    product = await food_repo.get_food_by_id(food_id)
    if not product:
        await message.answer("⚠️ Продукт не найден в базе.", reply_markup=get_main_reply_keyboard())
        return

    macros = food_repo.calculate_portion_macros(product, grams)
    title = f"{product['name']} ({int(grams)}г)"
    user_id = message.from_user.id

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
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    track_bju = bool(goals.get("track_bju", 1))
    macros_info = ""
    if track_bju:
        macros_info = f" ({macros['protein']}Б / {macros['fat']}Ж / {macros['carbs']}У)"

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    esc_title = html.escape(title)
    await message.answer(
        f"✅ Добавлено: [{type_name}] <b>{esc_title}</b> — {macros['calories']} ккал{macros_info}!\n\n"
        + dash_text,
        reply_markup=kb,
    )


@router.callback_query(F.data == "nutr_del_menu")
async def cb_nutr_del_menu(callback: CallbackQuery, local_date: str) -> None:
    """Show list of today's meals to delete."""
    user_id = callback.from_user.id
    meals = await nutrition_repo.get_meals_for_date(user_id, local_date)
    if not meals:
        await callback.answer("За сегодня еще нет записей для удаления.", show_alert=True)
        return

    await callback.message.edit_text(
        "🗑 <b>Выберите запись, которую хотите удалить:</b>",
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
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_nutr_del_item: %s", e)


@router.callback_query(F.data == "nutr_set_goal")
async def cb_nutr_set_goal(callback: CallbackQuery, state: FSMContext) -> None:
    """Open nutrition goals and tracking settings menu."""
    await state.clear()
    user_id = callback.from_user.id
    text, kb = await build_nutrition_goals_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_nutr_set_goal: %s", e)
    await callback.answer()


@router.callback_query(F.data == "nutr_toggle_bju")
async def cb_nutr_toggle_bju(callback: CallbackQuery) -> None:
    """Toggle BJU tracking on or off."""
    user_id = callback.from_user.id
    new_state = await nutrition_repo.toggle_track_bju(user_id)
    state_str = "включен ✅" if new_state else "выключен ❌"
    await callback.answer(f"Учет БЖУ {state_str}")

    text, kb = await build_nutrition_goals_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_nutr_toggle_bju: %s", e)


@router.callback_query(F.data == "nutr_toggle_water")
async def cb_nutr_toggle_water(callback: CallbackQuery) -> None:
    """Toggle water tracking on or off."""
    user_id = callback.from_user.id
    new_state = await nutrition_repo.toggle_track_water(user_id)
    state_str = "включен ✅" if new_state else "выключен ❌"
    await callback.answer(f"Трекер воды {state_str}")

    text, kb = await build_nutrition_goals_view(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_nutr_toggle_water: %s", e)


@router.callback_query(F.data == "nutr_change_cals")
async def cb_nutr_change_cals(callback: CallbackQuery, state: FSMContext) -> None:
    """Start setting daily calorie target."""
    user_id = callback.from_user.id
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    current_cals = goals.get("target_calories", 2000)

    await callback.message.edit_text(
        f"🎯 <b>Настройка суточной нормы калорий</b>\n\n"
        f"Текущая цель: <b>{current_cals} ккал</b>.\n\n"
        f"Введите желаемую суточную норму калорий (например: <code>2100</code>):\n"
        f"<i>(Или отправьте /cancel для отмены)</i>"
    )
    await state.set_state(NutritionStates.waiting_for_calories_goal)
    await callback.answer()


@router.message(NutritionStates.waiting_for_calories_goal)
async def process_calorie_goal(message: Message, state: FSMContext, local_date: str) -> None:
    """Validate calorie goal and optionally ask for BJU."""
    text = (message.text or "").strip()
    match = re.search(r"\d+", text)
    if not match:
        await message.answer("⚠️ Введите число (например: <code>2000</code>) или отправьте /cancel.")
        return

    cals = int(match.group(0))
    if cals < 500 or cals > 10000:
        await message.answer("⚠️ Пожалуйста, введите реалистичную норму калорий (от 500 до 10000 ккал).")
        return

    user_id = message.from_user.id
    goals = await nutrition_repo.get_nutrition_goals(user_id)
    track_bju = bool(goals.get("track_bju", 1))

    if not track_bju:
        # BJU tracking disabled - save calorie goal immediately
        await state.clear()
        await nutrition_repo.set_nutrition_goals(
            user_id=user_id,
            calories=cals,
            protein=0.0,
            fat=0.0,
            carbs=0.0,
        )
        dash_text, kb = await build_nutrition_view(user_id, local_date)
        await message.answer(
            f"🎉 <b>Ваша норма калорий успешно сохранена: {cals} ккал!</b>\n\n" + dash_text,
            reply_markup=kb,
        )
        return

    await state.update_data(target_calories=cals)
    await state.set_state(NutritionStates.waiting_for_bju_goal)

    await message.answer(
        f"✅ Цель по калориям: <b>{cals} ккал</b>!\n\n"
        f"Хотите также задать суточную норму <b>БЖУ (в граммах)</b>?\n"
        f"Отправьте три числа через пробел (Белки Жиры Углеводы), например:\n"
        f"<code>130 65 250</code>\n\n"
        f"Или отправьте <b>/skip</b>, чтобы оставить БЖУ необязательными."
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
        if len(nums) == 1:
            p = float(nums[0].replace(",", "."))
        elif len(nums) == 2:
            p = float(nums[0].replace(",", "."))
            f = float(nums[1].replace(",", "."))
        elif len(nums) >= 3:
            p = float(nums[0].replace(",", "."))
            f = float(nums[1].replace(",", "."))
            c = float(nums[2].replace(",", "."))
        else:
            await message.answer(
                "⚠️ Введите 3 числа (Белки Жиры Углеводы), например: <code>130 65 250</code>,\n"
                "или отправьте <b>/skip</b>, чтобы оставить БЖУ свободными."
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
    macros_msg = f" (Б: {p}г, Ж: {f}г, У: {c}г)" if (p > 0 or f > 0 or c > 0) else " (без ограничений по БЖУ)"
    await message.answer(
        f"🎉 <b>Ваша норма успешно сохранена!</b>\n"
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
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_water_add: %s", e)


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
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_water_undo: %s", e)


@router.callback_query(F.data == "water_custom")
async def cb_water_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to enter custom amount of water."""
    await callback.message.edit_text(
        "💧 <b>Добавление воды</b>\n\n"
        "Введите количество выпитой воды в миллилитрах (например: <code>350</code> или <code>600</code>):\n"
        "<i>(Отправьте /cancel для отмены)</i>"
    )
    await state.set_state(NutritionStates.waiting_for_water_amount)
    await callback.answer()


@router.message(NutritionStates.waiting_for_water_amount)
async def process_custom_water(message: Message, state: FSMContext, local_date: str) -> None:
    """Process custom water milliliters."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    match = re.search(r"-?\d+", text)

    if not match:
        await message.answer("⚠️ Введите число в миллилитрах (например: <code>300</code>) или отправьте /cancel.")
        return

    amount_ml = int(match.group(0))
    if amount_ml < 1 or amount_ml > 5000:
        await message.answer("⚠️ Пожалуйста, введите объем от 1 до 5000 мл (или /cancel).")
        return

    await state.clear()
    new_total = await water_repo.add_water(user_id, local_date, amount_ml)
    target_ml = await water_repo.get_water_target(user_id)

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(
        f"💧 Добавлено: <b>+{amount_ml} мл</b> воды! (Всего: {new_total}/{target_ml} мл)\n\n"
        + dash_text,
        reply_markup=kb,
    )


@router.message(Command("water"))
@router.message(Command("вода"))
async def cmd_quick_water(message: Message, local_date: str) -> None:
    """Quick water command e.g. /water 350 or /вода 500 with strict range validation."""
    user_id = message.from_user.id
    text = (message.text or "").strip()
    match = re.search(r"-?\d+", text)

    if not match:
        target_ml = await water_repo.get_water_target(user_id)
        cur_ml = await water_repo.get_daily_water(user_id, local_date)
        await message.answer(
            f"💧 <b>Трекер воды</b>\n"
            f"Сегодня выпито: <b>{cur_ml}</b> / {target_ml} мл\n\n"
            f"Чтобы добавить воду быстро, отправьте, например: <code>/water 250</code> или нажмите кнопку в меню «🥗 Питание»."
        )
        return

    amount_ml = int(match.group(0))
    if amount_ml < 1 or amount_ml > 5000:
        await message.answer("⚠️ Пожалуйста, укажите объем воды от 1 до 5000 мл (например: <code>/water 250</code>).")
        return

    new_total = await water_repo.add_water(user_id, local_date, amount_ml)
    target_ml = await water_repo.get_water_target(user_id)

    dash_text, kb = await build_nutrition_view(user_id, local_date)
    await message.answer(
        f"💧 Добавлено: <b>+{amount_ml} мл</b> воды! (Сегодня: {new_total}/{target_ml} мл)\n\n"
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
                await message.answer(f"✅ Вес записан: <b>{w_val} кг</b>!\n\n" + w_text, reply_markup=kb)
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
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_weight_menu: %s", e)
    await callback.answer()


@router.callback_query(F.data == "weight_enter")
async def cb_weight_enter(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type their weight."""
    await callback.message.edit_text(
        "⚖️ <b>Запись утреннего веса</b>\n\n"
        "Отправьте ваш текущий вес в килограммах (например: <code>72.4</code> или <code>73</code>):\n"
        "<i>(Отправьте /cancel для отмены)</i>"
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
        await message.answer("⚠️ Введите число (например: <code>72.5</code>) или отправьте /cancel.")
        return

    w_val = float(match.group(0).replace(",", "."))
    if w_val < 20.0 or w_val > 300.0:
        await message.answer("⚠️ Пожалуйста, введите реалистичный вес (от 20 до 300 кг).")
        return

    await state.clear()
    await weight_repo.log_weight(user_id, local_date, w_val)

    w_text, kb = await build_weight_view(user_id, local_date)
    await message.answer(f"✅ Вес записан: <b>{w_val} кг</b>!\n\n" + w_text, reply_markup=kb)


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
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_weight_toggle_reminder: %s", e)


@router.callback_query(F.data == "weight_choose_time")
async def cb_weight_choose_time(callback: CallbackQuery) -> None:
    """Show preset times for morning weight notification."""
    await callback.message.edit_text(
        "⏰ <b>Во сколько присылать утреннее напоминание о взвешивании?</b>\n\n"
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
    except TelegramBadRequest as e:
        logger.debug("Failed to edit text in cb_weight_set_time: %s", e)


@router.callback_query(F.data == "weight_custom_time")
async def cb_weight_custom_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type custom reminder time."""
    await callback.message.edit_text(
        "✍️ <b>Введите желаемое время утреннего напоминания</b>\n\n"
        "Формат: <code>ЧЧ:ММ</code> (например: <code>07:15</code>, <code>08:45</code> или <code>09:00</code>):\n"
        "<i>(Отправьте /cancel для отмены)</i>"
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
            "⚠️ Неверный формат времени. Введите время в формате <code>ЧЧ:ММ</code>, например: <code>07:45</code>.\n"
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
        f"✅ Время утреннего взвешивания установлено на <b>{time_str}</b> (напоминание включено)!\n\n"
        + w_text,
        reply_markup=kb,
    )
