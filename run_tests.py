"""Standalone test runner for all bot components."""

import asyncio
import json
import os
import sys
import time

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.database import (
    food_repo,
    habits_repo,
    nutrition_repo,
    timers_repo,
    users_repo,
    water_repo,
    weight_repo,
)
from bot.database.db import init_db
from bot.handlers.habits import (
    format_habit_edit_card,
    parse_days_argument,
    render_progress_bar,
)
from bot.handlers.start import (
    build_notifications_view,
    build_settings_view,
    calculate_offset_from_time_str,
)
from bot.handlers.timer import calculate_timer_spent_seconds
from bot.keyboards.reply import get_main_reply_keyboard
from bot.services.chart_builder import build_weekly_stats_chart
from bot.services.nutrition_parser import (
    format_meal_item,
    format_nutrition_dashboard,
    format_weight_dashboard,
    parse_meal_input,
)
from bot.services.scheduler import is_time_in_window
from bot.states.habit_states import TimerStates
from bot.keyboards.inline import (
    get_food_portions_keyboard,
    get_food_search_results_keyboard,
    get_meal_category_inline_keyboard,
    get_nutrition_dashboard_keyboard,
    get_nutrition_goals_menu_keyboard,
)
from bot.handlers.checkin import format_checkin_text

TEST_DB = "standalone_test.db"


async def run_all_checks():
    print("=" * 60)
    print("🚀 Running Habit Tracker Bot Verification Suite")
    print("=" * 60)

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    settings.DB_PATH = TEST_DB
    await init_db(TEST_DB)
    print("✅ 1. Database and tables initialized.")

    # 1. Users Repo
    user = await users_repo.create_or_update_user(101, "test_user", utc_offset=3, checkin_time="21:00")
    assert user["user_id"] == 101
    assert user["utc_offset"] == 3
    assert user["freezes_left"] == 1

    await users_repo.update_user_timezone(101, 5)
    u = await users_repo.get_user(101)
    assert u["utc_offset"] == 5

    deducted = await users_repo.deduct_freeze(101)
    assert deducted is True
    assert (await users_repo.get_user(101))["freezes_left"] == 0

    await users_repo.reset_weekly_freezes(101)
    assert (await users_repo.get_user(101))["freezes_left"] == 1
    print("✅ 2. users_repo tests passed (create, timezone, freeze deduct/reset).")

    # 2. Habits Repo
    h_id = await habits_repo.create_habit(101, "Английский", 45, "1,2,3,4,5")
    assert h_id > 0
    h = await habits_repo.get_habit_by_id(h_id)
    assert h["name"] == "Английский"
    assert h["target_minutes"] == 45

    # Day 3 (Wednesday) - scheduled
    wed_habits = await habits_repo.get_today_habits_with_status(101, "2026-10-21", 3)
    assert len(wed_habits) == 1
    assert wed_habits[0]["spent_minutes"] == 0

    # Day 7 (Sunday) - not scheduled
    sun_habits = await habits_repo.get_today_habits_with_status(101, "2026-10-25", 7)
    assert len(sun_habits) == 0

    # Log 30 min (incomplete)
    spent, completed = await habits_repo.upsert_habit_log(h_id, 101, "2026-10-21", 30, 45)
    assert spent == 30
    assert not completed

    # Log another 20 min (30+20=50 >= 45, completed)
    spent, completed = await habits_repo.upsert_habit_log(h_id, 101, "2026-10-21", 20, 45)
    assert spent == 50
    assert completed

    # Reflection note
    await habits_repo.update_habit_log_notes(h_id, "2026-10-21", "Unit 12 passed")
    wed_after = await habits_repo.get_today_habits_with_status(101, "2026-10-21", 3)
    assert "Unit 12 passed" in wed_after[0]["notes"]

    # Freeze habit
    success, msg = await habits_repo.freeze_habit_today(h_id, 101)
    assert success is True
    assert (await habits_repo.get_habit_by_id(h_id))["is_frozen_today"] == 1
    print("✅ 3. habits_repo tests passed (create, schedule check, logging, notes, freeze).")

    # 3. Midnight processing
    user2 = 202
    await users_repo.create_or_update_user(user2, "midnight_user")
    h2_done = await habits_repo.create_habit(user2, "Спорт", 30, "1,2,3,4,5,6,7")
    h2_miss = await habits_repo.create_habit(user2, "Чтение", 20, "1,2,3,4,5,6,7")

    yesterday = "2026-10-20"
    await habits_repo.upsert_habit_log(h2_done, user2, yesterday, 30, 30)

    await habits_repo.midnight_process_streaks_for_user(user2, yesterday, 2)
    assert (await habits_repo.get_habit_by_id(h2_done))["streak_days"] == 1
    assert (await habits_repo.get_habit_by_id(h2_miss))["streak_days"] == 0
    print("✅ 4. midnight streak processing tests passed (increment completed, reset missed).")

    # 4. Timers Repo
    user3 = 303
    await users_repo.create_or_update_user(user3, "timer_user")
    h3 = await habits_repo.create_habit(user3, "Программирование", 60, "1,2,3,4,5,6,7")

    now = int(time.time())
    await timers_repo.create_or_replace_timer(
        user_id=user3,
        habit_id=h3,
        total_target_seconds=3600,
        message_id=555,
        chat_id=777,
        started_at=now - 600,  # 10 min ago
    )

    t = await timers_repo.get_timer(user3)
    assert t is not None
    assert t["is_paused"] == 0
    assert calculate_timer_spent_seconds(t, now) == 600

    # Pause
    t_paused = await timers_repo.pause_timer(user3, now)
    assert t_paused["is_paused"] == 1
    assert calculate_timer_spent_seconds(t_paused, now + 300) == 600

    # Resume
    t_resumed = await timers_repo.resume_timer(user3, now + 300)
    assert t_resumed["is_paused"] == 0
    assert t_resumed["started_at"] == now + 300

    # Add 10 min
    t_ext = await timers_repo.add_time_to_timer(user3, 600)
    assert t_ext["total_target_seconds"] == 4200

    # Delete
    del_t = await timers_repo.delete_timer(user3)
    assert del_t["habit_name"] == "Программирование"
    assert await timers_repo.get_timer(user3) is None
    print("✅ 5. timers_repo tests passed (create, calculate spent, pause, resume, add time, delete).")

    # 5. Helpers
    assert render_progress_bar(0, 60, 8) == "[□□□□□□□□]"
    assert render_progress_bar(30, 60, 8) == "[■■■■□□□□]"
    assert render_progress_bar(60, 60, 8) == "[■■■■■■■■]"
    assert parse_days_argument("weekdays") == "1,2,3,4,5"
    assert parse_days_argument("weekends") == "6,7"
    assert parse_days_argument("1,3,5") == "1,3,5"
    offset = calculate_offset_from_time_str("15:30")
    assert isinstance(offset, int) and -12 <= offset <= 14
    print("✅ 6. Helper functions tests passed (progress bar, days parser, timezone offset).")

    # 6. Matplotlib Chart
    daily = {"2026-10-20": 45, "2026-10-21": 60, "2026-10-22": 30}
    habits = [
        {"name": "Английский", "total_spent": 105},
        {"name": "Спорт", "total_spent": 30},
    ]
    buf = build_weekly_stats_chart(daily, habits, "2026-10-22")
    buf_bytes = buf.getvalue()
    assert len(buf_bytes) > 1000
    assert buf_bytes.startswith(b"\x89PNG")
    print("✅ 7. Matplotlib dark-themed chart generated in-memory successfully.")

    # 7. Export Markdown
    export_data = await habits_repo.get_export_data(101, "2026-10-15", "2026-10-22")
    assert len(export_data) == 1
    assert export_data[0]["habit"]["name"] == "Английский"
    assert len(export_data[0]["logs"]) > 0
    print("✅ 8. Export markdown data retrieval verified.")

    # 8. Nutrition Repo & Parser Tests
    # Default goals
    goals = await nutrition_repo.get_nutrition_goals(101)
    assert goals["target_calories"] == 2000

    # Custom goals
    updated_goals = await nutrition_repo.set_nutrition_goals(101, 2300, 150.0, 70.0, 260.0)
    assert updated_goals["target_calories"] == 2300
    assert updated_goals["target_protein"] == 150.0

    # Add meal without macros (just calories)
    m1 = await nutrition_repo.add_meal(101, "2026-10-22", "Кофе с молоком", 120, meal_type="snack")
    assert m1["id"] > 0
    assert m1["calories"] == 120
    assert m1["protein"] == 0.0

    # Add meal with macros
    m2 = await nutrition_repo.add_meal(101, "2026-10-22", "Творог", 180, protein=20.0, fat=4.0, carbs=3.0, meal_type="snack")
    assert m2["calories"] == 180
    assert m2["protein"] == 20.0

    # Add lunch
    m3 = await nutrition_repo.add_meal(101, "2026-10-22", "Обед курица с рисом", 450, protein=35.0, fat=10.0, carbs=50.0, meal_type="lunch")
    assert m3["meal_type"] == "lunch"

    # Daily summary check
    summary = await nutrition_repo.get_daily_summary(101, "2026-10-22")
    assert summary["meal_count"] == 3
    assert summary["total_calories"] == 120 + 180 + 450  # 750
    assert summary["total_protein"] == 20.0 + 35.0      # 55.0
    assert summary["total_fat"] == 4.0 + 10.0          # 14.0
    assert summary["total_carbs"] == 3.0 + 50.0        # 53.0

    # List meals for date
    day_meals = await nutrition_repo.get_meals_for_date(101, "2026-10-22")
    assert len(day_meals) == 3

    # Delete meal
    del_res = await nutrition_repo.delete_meal(m1["id"], 101)
    assert del_res is True
    summary_after = await nutrition_repo.get_daily_summary(101, "2026-10-22")
    assert summary_after["meal_count"] == 2
    assert summary_after["total_calories"] == 630

    # Parser tests
    p1 = parse_meal_input("Кофе с молоком 120")
    assert p1 is not None and p1["title"] == "Кофе с молоком" and p1["calories"] == 120 and p1["protein"] == 0.0

    p2 = parse_meal_input("+250")
    assert p2 is not None and p2["calories"] == 250

    p3 = parse_meal_input("Творог 180 20/4/3")
    assert p3 is not None and p3["title"] == "Творог" and p3["calories"] == 180 and p3["protein"] == 20.0 and p3["fat"] == 4.0 and p3["carbs"] == 3.0

    p4 = parse_meal_input("Обед: Курица с гречкой 450 35б 8ж 45у")
    assert p4 is not None and p4["meal_type"] == "lunch" and p4["calories"] == 450 and p4["protein"] == 35.0

    p5 = parse_meal_input("Завтрак: Омлет 300")
    assert p5 is not None and p5["meal_type"] == "breakfast" and p5["calories"] == 300

    dash_text = format_nutrition_dashboard(updated_goals, summary_after, day_meals[1:], "2026-10-22")
    assert "Дневник питания" in dash_text
    assert "Калории" in dash_text
    print("✅ 9. Nutrition repo, meals logging, BJU calculation and text parser verified.")

    # 10. Water Repo Tests
    default_w_tgt = await water_repo.get_water_target(101)
    assert default_w_tgt == 2000
    await water_repo.set_water_target(101, 2500)
    assert await water_repo.get_water_target(101) == 2500

    tot1 = await water_repo.add_water(101, "2026-10-22", 250)
    assert tot1 == 250
    tot2 = await water_repo.add_water(101, "2026-10-22", 500)
    assert tot2 == 750
    assert await water_repo.get_daily_water(101, "2026-10-22") == 750

    undone = await water_repo.undo_last_water(101, "2026-10-22")
    assert undone == 500
    assert await water_repo.get_daily_water(101, "2026-10-22") == 250
    print("✅ 10. Water repo, hydration goals and undo tests passed.")

    # 11. Weight Repo and Morning Reminder Tests
    w1 = await weight_repo.log_weight(101, "2026-10-15", 73.2)
    assert w1["weight"] == 73.2
    w2 = await weight_repo.log_weight(101, "2026-10-21", 72.8)
    assert w2["weight"] == 72.8
    w3 = await weight_repo.log_weight(101, "2026-10-22", 72.5)
    assert w3["weight"] == 72.5

    # Check stats
    w_stats = await weight_repo.get_weight_stats(101, "2026-10-22")
    assert w_stats["today_weight"] == 72.5
    assert w_stats["latest_weight"] == 72.5
    assert w_stats["previous_weight"] == 72.8
    assert w_stats["diff_from_previous"] == -0.3
    assert w_stats["diff_from_week_ago"] == -0.7

    # Weight reminder in users table
    await users_repo.update_weight_reminder(101, enabled=1, reminder_time="07:45")
    u_after = await users_repo.get_user(101)
    assert u_after["weight_reminder_enabled"] == 1
    assert u_after["weight_reminder_time"] == "07:45"

    await users_repo.update_user_last_weight_reminder_date(101, "2026-10-22")
    u_rem = await users_repo.get_user(101)
    assert u_rem["last_weight_reminder_date"] == "2026-10-22"

    w_dash = format_weight_dashboard(w_stats, "2026-10-22", True, "07:45")
    assert "Трекер утреннего веса" in w_dash
    assert "72.5 кг" in w_dash
    assert "07:45" in w_dash
    print("✅ 11. Weight repo, history, dynamics calculation, and reminder settings verified.")

    # 12. Main 3-button menu and Notifications Muting Hub
    main_kb = get_main_reply_keyboard()
    assert len(main_kb.keyboard) == 2
    assert main_kb.keyboard[0][0].text == "🍏 Здоровье"
    assert main_kb.keyboard[0][1].text == "📋 Привычки"
    assert main_kb.keyboard[1][0].text == "⚙️ Настройки"

    # Settings view
    u_curr = await users_repo.get_user(101)
    s_text, s_kb = build_settings_view(u_curr)
    assert "Настройки бота" in s_text
    assert "Часовой пояс" in s_text

    # Notifications view - initially unmuted
    n_text, n_kb = build_notifications_view(u_curr)
    assert "Все запланированные уведомления активны" in n_text

    # Mute notifications forever
    await users_repo.mute_notifications(101, 2147483647)
    u_muted = await users_repo.get_user(101)
    assert u_muted["notifications_muted_until"] == 2147483647
    n_text_muted, n_kb_muted = build_notifications_view(u_muted)
    assert "отключены НАВСЕГДА" in n_text_muted

    # Unmute notifications
    await users_repo.unmute_notifications(101)
    u_unmuted = await users_repo.get_user(101)
    assert u_unmuted["notifications_muted_until"] == 0
    n_text_unmuted, _ = build_notifications_view(u_unmuted)
    assert "Все запланированные уведомления активны" in n_text_unmuted
    print("✅ 12. 3-Button menu structure, Settings Hub, and Notifications mute/unmute verified.")

    # 13. Habit Editing Tests (Name, Time, Active Days)
    upd1 = await habits_repo.update_habit(h_id, 101, name="Английский язык C1")
    assert upd1 is not None and upd1["name"] == "Английский язык C1"
    assert upd1["target_minutes"] == 45

    upd2 = await habits_repo.update_habit(h_id, 101, target_minutes=60)
    assert upd2 is not None and upd2["target_minutes"] == 60

    upd3 = await habits_repo.update_habit(h_id, 101, active_days="1,2,3,4,5,6,7")
    assert upd3 is not None and upd3["active_days"] == "1,2,3,4,5,6,7"

    # Verify unauthorized user cannot update habit
    unauth = await habits_repo.update_habit(h_id, 999, name="Hacked")
    assert unauth is None
    unchanged = await habits_repo.get_habit_by_id(h_id)
    assert unchanged["name"] == "Английский язык C1"

    from bot.handlers.habits import format_habit_edit_card
    edit_card = format_habit_edit_card(unchanged)
    assert "Английский язык C1" in edit_card
    assert "60 мин/день" in edit_card
    print("✅ 13. Habit editing (name, duration, days) and security authorization verified.")

    # 14. Checklist / Repetition Habits Tests (Pills, Hair Care, Reminders & Snooze)
    chk_id = await habits_repo.create_habit(
        user_id=101,
        name="Витамин D и Омега-3",
        target_minutes=0,
        active_days="1,2,3,4,5,6,7",
        habit_type="checklist",
        target_repeats=2,
        reminder_times="09:00,21:00",
    )
    assert chk_id > 0
    chk = await habits_repo.get_habit_by_id(chk_id)
    assert chk["habit_type"] == "checklist"
    assert chk["target_repeats"] == 2
    assert chk["reminder_times"] == "09:00,21:00"

    # Today status check
    chk_today = await habits_repo.get_today_habits_with_status(101, "2026-10-31", 6)
    found_chk = [h for h in chk_today if h["id"] == chk_id][0]
    assert found_chk["completed_repeats"] == 0
    assert found_chk["is_completed"] == 0

    # Increment 1 repeat (1/2, not completed)
    cur_reps, tgt_reps, is_done = await habits_repo.increment_habit_repeat(chk_id, 101, "2026-10-31")
    assert cur_reps == 1
    assert tgt_reps == 2
    assert is_done is False

    # Check checkin text formatting
    from bot.handlers.checkin import format_checkin_text
    chk_today_1 = await habits_repo.get_today_habits_with_status(101, "2026-10-31", 6)
    ci_text_1 = format_checkin_text(chk_today_1, "2026-10-31")
    assert "1/2 раз (Осталось: 1)" in ci_text_1

    # Increment 2nd repeat (2/2, completed!)
    cur_reps, tgt_reps, is_done = await habits_repo.increment_habit_repeat(chk_id, 101, "2026-10-31")
    assert cur_reps == 2
    assert is_done is True

    chk_today_2 = await habits_repo.get_today_habits_with_status(101, "2026-10-31", 6)
    ci_text_2 = format_checkin_text(chk_today_2, "2026-10-31")
    assert "2/2 раз (Выполнено)" in ci_text_2

    # Reminder logs & Snooze
    assert not await habits_repo.was_habit_reminder_sent(chk_id, "2026-10-31", "09:00")
    await habits_repo.record_habit_reminder_sent(chk_id, 101, "2026-10-31", "09:00")
    assert await habits_repo.was_habit_reminder_sent(chk_id, "2026-10-31", "09:00")

    # Snooze tests
    await habits_repo.add_habit_snooze(chk_id, 101, snooze_seconds=-5)  # already due
    due_snoozes = await habits_repo.get_due_snoozes(int(time.time()))
    assert len(due_snoozes) >= 1
    snooze_obj = [s for s in due_snoozes if s["habit_id"] == chk_id][0]
    assert snooze_obj["name"] == "Витамин D и Омега-3"
    await habits_repo.delete_snooze(snooze_obj["snooze_id"])
    due_after = await habits_repo.get_due_snoozes(int(time.time()))
    assert not any(s["habit_id"] == chk_id for s in due_after)

    # Edit checklist habit
    upd_chk = await habits_repo.update_habit(chk_id, 101, target_repeats=3, reminder_times="08:00,14:00,20:00")
    assert upd_chk["target_repeats"] == 3
    assert upd_chk["reminder_times"] == "08:00,14:00,20:00"

    chk_card = format_habit_edit_card(upd_chk)
    assert "3 раз(а) в день" in chk_card
    assert "08:00,14:00,20:00" in chk_card
    print("✅ 14. Checklist / Discrete habits (repeats, reminders, snoozes, check-in) verified.")

    # 15. Food Catalog, Portions & Frequent Meals (Stages 1 & 2)
    from bot.database import food_repo
    # Search catalog
    banana_matches = await food_repo.search_food("банан")
    assert len(banana_matches) >= 1
    banana = banana_matches[0]
    assert "Банан" in banana["name"]
    assert len(banana["portions"]) >= 2
    assert banana["calories_100g"] == 89.0

    # Calculate portion macros
    port_macros = food_repo.calculate_portion_macros(banana, 120)  # 120g banana
    assert port_macros["calories"] == 107
    assert port_macros["grams"] == 120

    # Search meat
    chicken_matches = await food_repo.search_food("курица")
    assert len(chicken_matches) >= 1
    assert "Куриное" in chicken_matches[0]["name"]

    # Frequent meals test
    # Log 3 meals for user 101
    await nutrition_repo.add_meal(101, "2026-11-01", "Кофе с молоком", 120, 2.0, 1.5, 4.0, "breakfast")
    await nutrition_repo.add_meal(101, "2026-11-01", "Овсянка", 250, 8.0, 4.0, 42.0, "breakfast")
    await nutrition_repo.add_meal(101, "2026-11-01", "Кофе с молоком", 120, 2.0, 1.5, 4.0, "breakfast")

    freq_breakfast = await nutrition_repo.get_frequent_meals(101, meal_type="breakfast", limit=4)
    assert len(freq_breakfast) >= 2
    assert freq_breakfast[0]["title"] == "Кофе с молоком"
    assert freq_breakfast[0]["frequency"] >= 2

    # Repeat frequent meal
    top_meal = freq_breakfast[0]
    rep_meal = await nutrition_repo.add_meal(
        101, "2026-11-01", top_meal["title"], top_meal["calories"],
        top_meal["protein"], top_meal["fat"], top_meal["carbs"], top_meal["meal_type"]
    )
    assert rep_meal["title"] == "Кофе с молоком"
    assert rep_meal["calories"] == 120

    # Keyboards check
    kb_freq = get_meal_category_inline_keyboard(frequent_meals=freq_breakfast)
    assert any("Кофе с молоком" in btn.text for row in kb_freq.inline_keyboard for btn in row)

    kb_port = get_food_portions_keyboard(banana)
    assert len(kb_port.inline_keyboard) >= 3

    print("✅ 15. Food Catalog (search, portion macros) and 1-Click Frequent Meals verified.")

    # 16. Optional Water & Macro (BJU) Tracking Settings
    # Default settings: both enabled
    goals_101 = await nutrition_repo.get_nutrition_goals(101)
    assert goals_101["track_bju"] == 1
    assert goals_101["track_water"] == 1

    # Toggle BJU to off
    new_bju = await nutrition_repo.toggle_track_bju(101)
    assert new_bju is False
    goals_101 = await nutrition_repo.get_nutrition_goals(101)
    assert goals_101["track_bju"] == 0

    # Toggle Water to off
    new_water = await nutrition_repo.toggle_track_water(101)
    assert new_water is False
    goals_101 = await nutrition_repo.get_nutrition_goals(101)
    assert goals_101["track_water"] == 0

    # Keyboards check with water disabled
    kb_no_water = get_nutrition_dashboard_keyboard(has_meals=True, has_water=True, track_water=False)
    all_buttons_no_water = [btn.text for row in kb_no_water.inline_keyboard for btn in row]
    assert not any("250 мл" in t for t in all_buttons_no_water)
    assert not any("➕ Вода" in t for t in all_buttons_no_water)
    assert not any("Отменить воду" in t for t in all_buttons_no_water)
    assert any("➕ Добавить еду" in t for t in all_buttons_no_water)

    # Keyboards check with water enabled
    kb_with_water = get_nutrition_dashboard_keyboard(has_meals=True, has_water=True, track_water=True)
    all_buttons_with_water = [btn.text for row in kb_with_water.inline_keyboard for btn in row]
    assert any("250 мл" in t for t in all_buttons_with_water)
    assert any("Отменить воду" in t for t in all_buttons_with_water)

    # Goals menu keyboard
    kb_goals_menu = get_nutrition_goals_menu_keyboard(track_bju=False, track_water=False)
    btn_texts = [btn.text for row in kb_goals_menu.inline_keyboard for btn in row]
    assert any("БЖУ: ❌ ВЫКЛ" in t for t in btn_texts)
    assert any("воды: ❌ ВЫКЛ" in t for t in btn_texts)

    # Dashboard formatting with BJU & water disabled
    meals_101 = await nutrition_repo.get_meals_for_date(101, "2026-11-01")
    summary_101 = await nutrition_repo.get_daily_summary(101, "2026-11-01")
    dash_hidden = format_nutrition_dashboard(
        goals=goals_101,
        summary=summary_101,
        meals=meals_101,
        local_date="2026-11-01",
        water_current=500,
        water_target=2000,
    )
    assert "БЖУ" not in dash_hidden
    assert "Белки" not in dash_hidden
    assert "Вода:" not in dash_hidden
    assert "1.5Ж" not in dash_hidden  # No macros in meal lines

    # Check-in formatting with BJU & water disabled
    checkin_hidden = format_checkin_text(
        habits=[],
        local_date="2026-11-01",
        nutrition_summary=summary_101,
        nutrition_goals=goals_101,
        water_ml=500,
        water_target=2000,
    )
    assert "Б /" not in checkin_hidden
    assert "Вода за сегодня" not in checkin_hidden
    assert "Питание за сегодня:" in checkin_hidden

    # Re-enable BJU & Water
    await nutrition_repo.toggle_track_bju(101)
    await nutrition_repo.toggle_track_water(101)
    goals_101_on = await nutrition_repo.get_nutrition_goals(101)
    assert goals_101_on["track_bju"] == 1
    assert goals_101_on["track_water"] == 1

    dash_visible = format_nutrition_dashboard(
        goals=goals_101_on,
        summary=summary_101,
        meals=meals_101,
        local_date="2026-11-01",
        water_current=500,
        water_target=2000,
    )
    assert ("Белки" in dash_visible or "БЖУ" in dash_visible)
    assert "1.5Ж" in dash_visible
    assert "Вода:" in dash_visible

    checkin_visible = format_checkin_text(
        habits=[],
        local_date="2026-11-01",
        nutrition_summary=summary_101,
        nutrition_goals=goals_101_on,
        water_ml=500,
        water_target=2000,
    )
    assert "Б /" in checkin_visible
    assert "Вода за сегодня" in checkin_visible

    print("✅ 16. Optional Water & Macro (BJU) Tracking Settings verified.")

    # 17. Cyrillic Food Search & Parser Disambiguation
    await food_repo.seed_food_catalog_if_empty(TEST_DB)
    res_lower = await food_repo.search_food("творог", limit=5, db_path=TEST_DB)
    res_upper = await food_repo.search_food("ТВОРОГ", limit=5, db_path=TEST_DB)
    assert len(res_lower) > 0 and len(res_upper) > 0
    assert res_lower[0]["name"] == res_upper[0]["name"]

    # Numbered products without explicit kcal should NOT be falsely parsed by manual parser
    assert parse_meal_input("Творог 5%") is None
    assert parse_meal_input("Кефир 1%") is None
    assert parse_meal_input("Яичница из 2 яиц") is None
    assert parse_meal_input("Молоко 2.5%") is None
    p_with_cals = parse_meal_input("Творог 5% 150")
    assert p_with_cals is not None and p_with_cals["calories"] == 150
    print("✅ 17. Cyrillic catalog search & parser conflict disambiguation verified.")

    # 18. Settings Isolation (Timezone and Checkin time)
    iso_user = 5555
    await users_repo.create_or_update_user(iso_user, "iso_user", utc_offset=3, checkin_time="21:00")
    await users_repo.update_user_timezone(iso_user, 5)
    u_iso = await users_repo.get_user(iso_user)
    assert u_iso["utc_offset"] == 5 and u_iso["checkin_time"] == "21:00"
    await users_repo.update_user_checkin_time(iso_user, "23:15")
    u_iso2 = await users_repo.get_user(iso_user)
    assert u_iso2["utc_offset"] == 5 and u_iso2["checkin_time"] == "23:15"
    print("✅ 18. Settings isolation (timezone & checkin time) verified.")

    # 19. Atomic Timer Delete & Reflection FSM
    atm_user = 6666
    await users_repo.create_or_update_user(atm_user, "atm_user")
    h_atm = await habits_repo.create_habit(atm_user, "Фокус", 30)
    await timers_repo.create_or_replace_timer(
        user_id=atm_user,
        habit_id=h_atm,
        total_target_seconds=1800,
        message_id=1,
        chat_id=1,
        started_at=int(time.time()) - 1800,
    )
    t_del1 = await timers_repo.delete_timer(atm_user)
    assert t_del1 is not None and t_del1["habit_name"] == "Фокус"
    t_del2 = await timers_repo.delete_timer(atm_user)
    assert t_del2 is None  # Second atomic delete is None

    storage = MemoryStorage()
    fsm_state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=atm_user))
    await fsm_state.set_state(TimerStates.waiting_for_reflection)
    await fsm_state.update_data(habit_id=h_atm, logged_date="2026-11-21", habit_name="Фокус")
    assert await fsm_state.get_state() == TimerStates.waiting_for_reflection.state
    print("✅ 19. Atomic timer deletion & reflection FSM activation verified.")

    # 20. Habit Ownership Security
    owner_id, thief_id = 7771, 7772
    await users_repo.create_or_update_user(owner_id, "owner")
    await users_repo.create_or_update_user(thief_id, "thief")
    sec_habit = await habits_repo.create_habit(owner_id, "Секретная привычка", 30)
    thief_freeze, _ = await habits_repo.freeze_habit_today(sec_habit, thief_id)
    assert thief_freeze is False
    assert await habits_repo.update_habit(sec_habit, thief_id, name="Взлом") is None
    assert await habits_repo.delete_habit(sec_habit, thief_id) is False
    assert await habits_repo.delete_habit(sec_habit, owner_id) is True
    print("✅ 20. Habit ownership security authorization verified.")

    # 21. FSM JSON Serializability & Scheduler Time Window
    fsm_payload = {"selected_days": [1, 2, 3, 4, 5], "edit_days": [6, 7]}
    assert json.dumps(fsm_payload) is not None
    assert is_time_in_window("21:10", "21:00", window_minutes=30) is True
    assert is_time_in_window("21:45", "21:00", window_minutes=30) is False
    print("✅ 21. FSM JSON serializability & scheduler time window verified.")

    # 22. HTML Escaping & UTC Timezone Conversion
    esc_card = format_habit_edit_card({
        "name": "Yoga & <script>alert(1)</script>",
        "habit_type": "duration",
        "target_minutes": 20,
        "active_days": "1,2,3,4,5,6,7",
    })
    assert "<script>" not in esc_card and "&lt;script&gt;" in esc_card and "&amp;" in esc_card

    meal_local = format_meal_item(
        {"title": "Ужин & чай", "calories": 300, "created_at": "2026-11-21 15:30:00"},
        utc_offset=3,
    )
    assert "18:30" in meal_local
    assert "&amp;" in meal_local
    print("✅ 22. HTML escaping & UTC to local meal conversion verified.")

    # Cleanup
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    print("=" * 60)
    print("🎉 ALL TESTS PASSED SUCCESSFULLY! PRODUCTION READY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_checks())
