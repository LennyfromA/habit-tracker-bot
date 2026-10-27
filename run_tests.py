"""Standalone test runner for all bot components."""

import asyncio
import os
import sys
import time

from bot.config import settings
from bot.database import (
    habits_repo,
    nutrition_repo,
    timers_repo,
    users_repo,
    water_repo,
    weight_repo,
)
from bot.database.db import init_db
from bot.handlers.habits import parse_days_argument, render_progress_bar
from bot.handlers.start import calculate_offset_from_time_str
from bot.handlers.timer import calculate_timer_spent_seconds
from bot.services.chart_builder import build_weekly_stats_chart
from bot.services.nutrition_parser import (
    format_nutrition_dashboard,
    format_weight_dashboard,
    parse_meal_input,
)

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

    # Cleanup
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    print("=" * 60)
    print("🎉 ALL TESTS PASSED SUCCESSFULLY! PRODUCTION READY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_checks())
