"""Comprehensive test suite for Habit Tracker Bot."""

import asyncio
import os
import time
import pytest
import aiosqlite

from bot.database.db import get_db, init_db
from bot.database import habits_repo, timers_repo, users_repo
from bot.handlers.habits import parse_days_argument, render_progress_bar
from bot.handlers.start import calculate_offset_from_time_str
from bot.handlers.timer import calculate_timer_spent_seconds
from bot.services.chart_builder import build_weekly_stats_chart


import pytest_asyncio

TEST_DB = "test_habit_tracker.db"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    """Setup and teardown a temporary SQLite database."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    from bot.config import settings
    monkeypatch.setattr(settings, "DB_PATH", TEST_DB)

    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.mark.asyncio
async def test_users_repository():
    """Test user registration, timezone updates, and freeze operations."""
    user = await users_repo.create_or_update_user(1001, "alex_test", utc_offset=3, checkin_time="21:00")
    assert user["user_id"] == 1001
    assert user["utc_offset"] == 3
    assert user["freezes_left"] == 1

    # Update timezone
    await users_repo.update_user_timezone(1001, 5)
    updated = await users_repo.get_user(1001)
    assert updated["utc_offset"] == 5

    # Deduct freeze
    deducted = await users_repo.deduct_freeze(1001)
    assert deducted is True
    updated = await users_repo.get_user(1001)
    assert updated["freezes_left"] == 0

    # Deduct again when 0
    deducted_again = await users_repo.deduct_freeze(1001)
    assert deducted_again is False

    # Reset freezes
    await users_repo.reset_weekly_freezes(1001)
    updated = await users_repo.get_user(1001)
    assert updated["freezes_left"] == 1


@pytest.mark.asyncio
async def test_habits_repository_and_logs():
    """Test habit creation, daily status, progress logging, and streak freezes."""
    user_id = 2001
    await users_repo.create_or_update_user(user_id, "bob_test", utc_offset=3)

    habit_id = await habits_repo.create_habit(
        user_id=user_id,
        name="Английский",
        target_minutes=45,
        active_days="1,2,3,4,5",
    )
    assert habit_id > 0

    habit = await habits_repo.get_habit_by_id(habit_id)
    assert habit["name"] == "Английский"
    assert habit["target_minutes"] == 45
    assert habit["streak_days"] == 0

    # Today is scheduled (let's test day 3 = Wednesday)
    today_habits = await habits_repo.get_today_habits_with_status(user_id, "2026-10-21", 3)
    assert len(today_habits) == 1
    assert today_habits[0]["spent_minutes"] == 0
    assert today_habits[0]["is_completed"] == 0

    # Day 6 = Saturday (not scheduled)
    sat_habits = await habits_repo.get_today_habits_with_status(user_id, "2026-10-24", 6)
    assert len(sat_habits) == 0

    # Log 20 minutes (not completed)
    spent, completed = await habits_repo.upsert_habit_log(
        habit_id=habit_id,
        user_id=user_id,
        logged_date="2026-10-21",
        add_minutes=20,
        target_minutes=45,
    )
    assert spent == 20
    assert completed is False

    # Log additional 30 minutes (20+30=50 >= 45, completed!)
    spent, completed = await habits_repo.upsert_habit_log(
        habit_id=habit_id,
        user_id=user_id,
        logged_date="2026-10-21",
        add_minutes=30,
        target_minutes=45,
    )
    assert spent == 50
    assert completed is True

    # Check reflection note
    await habits_repo.update_habit_log_notes(habit_id, "2026-10-21", "Прошел Unit 12")
    log = await habits_repo.get_habit_log(habit_id, "2026-10-21") if hasattr(habits_repo, "get_habit_log") else None
    today_h = await habits_repo.get_today_habits_with_status(user_id, "2026-10-21", 3)
    assert today_h[0]["notes"] == "Прошел Unit 12"

    # Test Streak freeze
    success, msg = await habits_repo.freeze_habit_today(habit_id, user_id)
    assert success is True
    habit_after_freeze = await habits_repo.get_habit_by_id(habit_id)
    assert habit_after_freeze["is_frozen_today"] == 1


@pytest.mark.asyncio
async def test_midnight_streak_processing():
    """Test streak increment and reset at midnight."""
    user_id = 3001
    await users_repo.create_or_update_user(user_id, "streak_tester", utc_offset=3)
    h_done_id = await habits_repo.create_habit(user_id, "Спорт", 30, "1,2,3,4,5,6,7")
    h_miss_id = await habits_repo.create_habit(user_id, "Медитация", 15, "1,2,3,4,5,6,7")

    # Complete Спорт for yesterday
    yesterday = "2026-10-20"
    yesterday_weekday = 2
    await habits_repo.upsert_habit_log(h_done_id, user_id, yesterday, 30, 30)

    # Process midnight for yesterday
    await habits_repo.midnight_process_streaks_for_user(user_id, yesterday, yesterday_weekday)

    h_done = await habits_repo.get_habit_by_id(h_done_id)
    h_miss = await habits_repo.get_habit_by_id(h_miss_id)

    assert h_done["streak_days"] == 1
    assert h_miss["streak_days"] == 0


@pytest.mark.asyncio
async def test_timers_repository_and_calculations():
    """Test timer lifecycle: start, pause, resume, add time, stop."""
    user_id = 4001
    await users_repo.create_or_update_user(user_id, "timer_user")
    h_id = await habits_repo.create_habit(user_id, "Код", 50, "1,2,3,4,5,6,7")

    now = int(time.time())
    await timers_repo.create_or_replace_timer(
        user_id=user_id,
        habit_id=h_id,
        total_target_seconds=3000,
        message_id=99,
        chat_id=123,
        started_at=now - 600,  # 10 minutes ago
    )

    timer = await timers_repo.get_timer(user_id)
    assert timer is not None
    assert timer["is_paused"] == 0

    # Calculate elapsed
    spent = calculate_timer_spent_seconds(timer, now)
    assert spent == 600

    # Pause timer
    paused = await timers_repo.pause_timer(user_id, now)
    assert paused["is_paused"] == 1
    assert paused["elapsed_before_pause"] == 600

    # Wait 200 virtual seconds, elapsed should remain 600 because paused
    spent_paused = calculate_timer_spent_seconds(paused, now + 200)
    assert spent_paused == 600

    # Resume timer
    resumed = await timers_repo.resume_timer(user_id, now + 200)
    assert resumed["is_paused"] == 0
    assert resumed["started_at"] == now + 200

    # Add 10 minutes (+600 seconds)
    extended = await timers_repo.add_time_to_timer(user_id, 600)
    assert extended["total_target_seconds"] == 3600

    # Delete timer
    deleted = await timers_repo.delete_timer(user_id)
    assert deleted["habit_name"] == "Код"
    assert await timers_repo.get_timer(user_id) is None


def test_helper_progress_bar():
    """Test progress bar visual formatting."""
    assert render_progress_bar(0, 60, 8) == "[□□□□□□□□]"
    assert render_progress_bar(30, 60, 8) == "[■■■■□□□□]"
    assert render_progress_bar(60, 60, 8) == "[■■■■■■■■]"


def test_days_argument_parser():
    """Test /new command days argument parser."""
    assert parse_days_argument("weekdays") == "1,2,3,4,5"
    assert parse_days_argument("weekends") == "6,7"
    assert parse_days_argument("1,3,5") == "1,3,5"
    assert parse_days_argument("daily") == "1,2,3,4,5,6,7"


def test_timezone_offset_calculator():
    """Test UTC offset parsing from time string."""
    offset = calculate_offset_from_time_str("12:00")
    assert isinstance(offset, int)
    assert -12 <= offset <= 14


def test_matplotlib_in_memory_chart():
    """Test chart builder generates a non-empty image buffer without errors."""
    daily = {"2026-10-20": 45, "2026-10-21": 60, "2026-10-22": 30}
    habits = [
        {"name": "Английский", "total_spent": 105},
        {"name": "Спорт", "total_spent": 30},
    ]
    buf = build_weekly_stats_chart(daily, habits, "2026-10-22")
    data = buf.getvalue()
    assert len(data) > 1000
    assert data.startswith(b"\x89PNG")  # Valid PNG signature
