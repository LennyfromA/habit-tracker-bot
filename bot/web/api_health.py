"""REST API endpoints for Nutrition, Water, and Weight tracking."""

import logging
from typing import Any, Dict, Optional

from aiohttp import web

from bot.database import food_repo, nutrition_repo, water_repo, weight_repo
from bot.services.nutrition_parser import parse_meal_input
from bot.services.widget_launcher import sync_widget_message

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


@routes.get("/api/food/search")
async def api_food_search(request: web.Request) -> web.Response:
    """Fast search in food catalog with portions."""
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"ok": True, "data": []})

    results = await food_repo.search_food(query, limit=8)
    return web.json_response({"ok": True, "data": results})


@routes.post("/api/meals")
async def api_add_meal(request: web.Request) -> web.Response:
    """Universal meal logging supporting 4 input modes."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    mode = str(data.get("mode") or "quick_text").strip().lower()
    meal_type = str(data.get("meal_type") or "snack").strip().lower()
    if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
        meal_type = "snack"

    logged_meal: Optional[Dict[str, Any]] = None

    if mode == "catalog":
        try:
            food_id = int(data.get("food_id"))
            grams = float(data.get("grams", 100))
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректный food_id или граммы"}, status=400)

        if grams <= 0 or grams > 5000:
            return web.json_response({"ok": False, "error": "Граммовка должна быть от 1 до 5000г"}, status=400)

        food = await food_repo.get_food_by_id(food_id)
        if not food:
            return web.json_response({"ok": False, "error": "Продукт не найден в каталоге"}, status=404)

        macros = food_repo.calculate_portion_macros(food, grams)
        portion_name = data.get("portion_name")
        if portion_name:
            title = f"{food['name']} ({portion_name})"
        else:
            title = f"{food['name']} ({round(grams)}г)"

        logged_meal = await nutrition_repo.add_meal(
            user_id=user_id,
            logged_date=local_date,
            title=title,
            calories=macros["calories"],
            protein=macros["protein"],
            fat=macros["fat"],
            carbs=macros["carbs"],
            meal_type=meal_type,
        )

    elif mode == "repeat":
        try:
            meal_id = int(data.get("meal_id"))
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректный meal_id"}, status=400)

        prev_meal = await nutrition_repo.get_meal_by_id(meal_id, user_id)
        if not prev_meal:
            return web.json_response({"ok": False, "error": "Блюдо не найдено"}, status=404)

        logged_meal = await nutrition_repo.add_meal(
            user_id=user_id,
            logged_date=local_date,
            title=prev_meal["title"],
            calories=prev_meal["calories"],
            protein=prev_meal.get("protein", 0.0),
            fat=prev_meal.get("fat", 0.0),
            carbs=prev_meal.get("carbs", 0.0),
            meal_type=meal_type or prev_meal.get("meal_type", "snack"),
        )

    elif mode == "quick_text":
        text = str(data.get("text") or "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "Введите текст блюда"}, status=400)

        parsed = parse_meal_input(text, default_meal_type=meal_type)
        if not parsed:
            return web.json_response(
                {
                    "ok": False,
                    "error": (
                        "Не удалось распознать приём пищи. "
                        "Укажите название и калории, например: «Банан 105» или «Творог 150 20/4/3»"
                    ),
                },
                status=400,
            )

        logged_meal = await nutrition_repo.add_meal(
            user_id=user_id,
            logged_date=local_date,
            title=parsed["title"],
            calories=parsed["calories"],
            protein=parsed.get("protein", 0.0),
            fat=parsed.get("fat", 0.0),
            carbs=parsed.get("carbs", 0.0),
            meal_type=parsed.get("meal_type", meal_type),
        )

    elif mode == "manual":
        title = str(data.get("title") or "").strip()
        if not title or len(title) > 100:
            return web.json_response(
                {"ok": False, "error": "Название должно содержать от 1 до 100 символов"},
                status=400,
            )

        try:
            calories = int(data.get("calories", 0))
            if calories < 0 or calories > 10000:
                return web.json_response({"ok": False, "error": "Калории должны быть от 0 до 10000"}, status=400)
            protein = float(data.get("protein", 0.0))
            fat = float(data.get("fat", 0.0))
            carbs = float(data.get("carbs", 0.0))
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректные значения КБЖУ"}, status=400)

        logged_meal = await nutrition_repo.add_meal(
            user_id=user_id,
            logged_date=local_date,
            title=title,
            calories=calories,
            protein=protein,
            fat=fat,
            carbs=carbs,
            meal_type=meal_type,
        )

    else:
        return web.json_response(
            {"ok": False, "error": "Неподдерживаемый mode. Используйте catalog, repeat, quick_text или manual"},
            status=400,
        )

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "health")

    return web.json_response({"ok": True, "data": logged_meal, "synced": synced}, status=201)


@routes.delete("/api/meals/{meal_id}")
async def api_delete_meal(request: web.Request) -> web.Response:
    """Delete logged meal with ownership check."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        meal_id = int(request.match_info["meal_id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Invalid meal_id"}, status=400)

    meal = await nutrition_repo.get_meal_by_id(meal_id, user_id)
    if not meal:
        return web.json_response({"ok": False, "error": "Запись приема пищи не найдена"}, status=404)

    success = await nutrition_repo.delete_meal(meal_id, user_id)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "health")

    return web.json_response({"ok": success, "synced": synced})


@routes.patch("/api/nutrition/goals")
async def api_update_nutrition_goals(request: web.Request) -> web.Response:
    """Update calorie/macro goals, tracking toggles, and water target."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    current_goals = await nutrition_repo.get_nutrition_goals(user_id)

    target_calories = data.get("target_calories")
    if target_calories is not None:
        try:
            target_calories = int(target_calories)
            if target_calories < 500 or target_calories > 10000:
                return web.json_response(
                    {"ok": False, "error": "Цель по калориям должна быть от 500 до 10000 ккал"},
                    status=400,
                )
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректная цель по калориям"}, status=400)

    cals = target_calories if target_calories is not None else current_goals.get("target_calories", 2000)
    prot = float(data.get("target_protein", current_goals.get("target_protein", 0.0)))
    fat = float(data.get("target_fat", current_goals.get("target_fat", 0.0)))
    carbs = float(data.get("target_carbs", current_goals.get("target_carbs", 0.0)))

    await nutrition_repo.set_nutrition_goals(
        user_id=user_id,
        calories=cals,
        protein=prot,
        fat=fat,
        carbs=carbs,
    )

    track_bju = data.get("track_bju")
    track_water = data.get("track_water")
    if track_bju is not None or track_water is not None:
        await nutrition_repo.set_nutrition_tracking_flags(
            user_id=user_id,
            track_bju=bool(track_bju) if track_bju is not None else None,
            track_water=bool(track_water) if track_water is not None else None,
        )

    water_target_ml = data.get("water_target_ml")
    if water_target_ml is not None:
        try:
            water_target_ml = int(water_target_ml)
            if water_target_ml < 500 or water_target_ml > 10000:
                return web.json_response(
                    {"ok": False, "error": "Цель по воде должна быть от 500 до 10000 мл"},
                    status=400,
                )
            await water_repo.set_water_target(user_id, water_target_ml)
        except (ValueError, TypeError):
            return web.json_response({"ok": False, "error": "Некорректная цель по воде"}, status=400)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "health")

    updated_goals = await nutrition_repo.get_nutrition_goals(user_id)
    return web.json_response({"ok": True, "data": updated_goals, "synced": synced})


@routes.post("/api/water")
async def api_add_water(request: web.Request) -> web.Response:
    """Log water intake in ml."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
        amount_ml = int(data.get("amount_ml"))
    except Exception:
        return web.json_response({"ok": False, "error": "amount_ml is required and must be an integer"}, status=400)

    if amount_ml < 1 or amount_ml > 5000:
        return web.json_response(
            {"ok": False, "error": "Объем воды должен быть от 1 до 5000 мл"},
            status=400,
        )

    new_total = await water_repo.add_water(user_id, local_date, amount_ml)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "health")

    return web.json_response({"ok": True, "data": {"current_ml": new_total}, "synced": synced})


@routes.post("/api/water/undo")
async def api_undo_water(request: web.Request) -> web.Response:
    """Undo the last water entry for today."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    removed_ml = await water_repo.undo_last_water(user_id, local_date)
    current_ml = await water_repo.get_daily_water(user_id, local_date)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "health")

    return web.json_response({
        "ok": True,
        "data": {
            "current_ml": current_ml,
            "removed_ml": removed_ml or 0,
        },
        "synced": synced,
    })


@routes.post("/api/weight")
async def api_log_weight(request: web.Request) -> web.Response:
    """Log morning weight entry."""
    auth: Dict[str, Any] = request["auth"]
    user_id: int = auth["user_id"]
    local_date: str = auth["local_date"]
    wid: Optional[str] = request.get("wid")
    bot = request.app.get("bot")

    try:
        data = await request.json()
        weight = float(data.get("weight"))
    except Exception:
        return web.json_response({"ok": False, "error": "weight is required and must be a number"}, status=400)

    if weight < 20.0 or weight > 300.0:
        return web.json_response(
            {"ok": False, "error": "Вес должен быть от 20.0 до 300.0 кг"},
            status=400,
        )

    note = data.get("note")
    if note is not None:
        note = str(note).strip() or None

    entry = await weight_repo.log_weight(user_id, local_date, weight, note)

    synced = False
    if bot:
        synced = await sync_widget_message(bot, user_id, wid, "health")

    return web.json_response({"ok": True, "data": entry, "synced": synced})
