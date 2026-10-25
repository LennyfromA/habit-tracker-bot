"""Standalone test runner for all bot components."""

import asyncio
import os
import sys
import time

from bot.config import settings
from bot.database import habits_repo, timers_repo, users_repo
from bot.database.db import init_db
from bot.handlers.habits import parse_days_argument, render_progress_bar
from bot.handlers.start import calculate_offset_from_time_str
from bot.handlers.timer import calculate_timer_spent_seconds
from bot.services.chart_builder import build_weekly_stats_chart

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

    # Cleanup
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    print("=" * 60)
    print("🎉 ALL TESTS PASSED SUCCESSFULLY! PRODUCTION READY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_checks())
