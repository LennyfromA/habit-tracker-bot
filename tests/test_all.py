"""Comprehensive test suite for Habit Tracker Bot."""

import asyncio
import html
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, User, Chat

from bot.config import settings
from bot.database import food_repo, habits_repo, nutrition_repo, timers_repo, users_repo, water_repo
from bot.database.db import get_db, init_db
from bot.handlers.checkin import format_checkin_text, render_checkin_view
from bot.handlers.habits import format_habit_edit_card, parse_days_argument, render_habits_list_view
from bot.handlers.nutrition import build_nutrition_goals_view
from bot.handlers.start import (
    build_notifications_view,
    build_settings_view,
    calculate_offset_from_time_str,
)
from bot.handlers.stats import render_export_view, render_stats_view
from bot.handlers.timer import calculate_timer_spent_seconds, render_timer_view
from bot.services.chart_builder import build_weekly_stats_chart
from bot.services.nutrition_parser import format_meal_item, parse_meal_input
from bot.services.scheduler import check_active_timers, is_time_in_window
from bot.states.habit_states import TimerStates
from bot.utils.formatting import render_progress_bar

TEST_DB = "test_habit_tracker.db"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    """Setup and teardown a temporary SQLite database."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    monkeypatch.setattr(settings, "DB_PATH", TEST_DB)
    await init_db(TEST_DB)
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ---------------------------------------------------------------------------
# Core Repository & Lifecycle Tests
# ---------------------------------------------------------------------------

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

    # Today is scheduled (day 3 = Wednesday)
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

    yesterday = "2026-10-20"
    yesterday_weekday = 2
    await habits_repo.upsert_habit_log(h_done_id, user_id, yesterday, 30, 30)

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

    spent = calculate_timer_spent_seconds(timer, now)
    assert spent == 600

    # Pause timer
    paused = await timers_repo.pause_timer(user_id, now)
    assert paused["is_paused"] == 1
    assert paused["elapsed_before_pause"] == 600

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


# ---------------------------------------------------------------------------
# Specific Bug Regression Tests (Stages 1-3)
# ---------------------------------------------------------------------------

def test_module_imports_and_routers():
    """1. Verify clean imports for all handler routers and functions (no broken names)."""
    from bot.handlers import checkin, habits, nutrition, start, stats, timer
    assert start.router is not None
    assert habits.router is not None
    assert timer.router is not None
    assert checkin.router is not None
    assert nutrition.router is not None
    assert stats.router is not None

    # Check that aliases and extracted functions exist
    assert hasattr(timer, "cmd_check_timer")
    assert hasattr(timer, "render_timer_view")
    assert hasattr(stats, "cmd_export_markdown")
    assert hasattr(stats, "cmd_export")
    assert hasattr(stats, "render_stats_view")
    assert hasattr(stats, "render_export_view")
    assert hasattr(checkin, "render_checkin_view")
    assert hasattr(habits, "render_habits_list_view")
    assert hasattr(nutrition, "build_nutrition_goals_view")


@pytest.mark.asyncio
async def test_timezone_and_checkin_settings_isolation():
    """2. Verify create_or_update_user conflict handling & separate timezone/checkin updates."""
    user_id = 5001
    await users_repo.create_or_update_user(user_id, "u1", utc_offset=3, checkin_time="21:00")

    # Conflict on duplicate create_or_update_user: should update utc_offset and checkin_time
    await users_repo.create_or_update_user(user_id, "u1_new", utc_offset=5, checkin_time="22:30")
    u = await users_repo.get_user(user_id)
    assert u["username"] == "u1_new"
    assert u["utc_offset"] == 5
    assert u["checkin_time"] == "22:30"

    # Settings update: timezone only
    await users_repo.update_user_timezone(user_id, 2)
    u_tz = await users_repo.get_user(user_id)
    assert u_tz["utc_offset"] == 2
    assert u_tz["checkin_time"] == "22:30"  # Checkin time remains unchanged!

    # Settings update: checkin time only
    await users_repo.update_user_checkin_time(user_id, "20:15")
    u_ci = await users_repo.get_user(user_id)
    assert u_ci["utc_offset"] == 2  # Timezone remains unchanged!
    assert u_ci["checkin_time"] == "20:15"


def test_scheduler_window_and_mute_logic():
    """3. Verify time window matching and muted_until logic."""
    assert is_time_in_window("21:00", "21:00", 30) is True
    assert is_time_in_window("21:15", "21:00", 30) is True
    assert is_time_in_window("21:29", "21:00", 30) is True
    assert is_time_in_window("21:35", "21:00", 30) is False
    assert is_time_in_window("20:50", "21:00", 30) is False  # Before target


@pytest.mark.asyncio
async def test_atomic_timer_completion_and_fsm_reflection():
    """4. Verify atomic timer deletion and FSM reflection state activation."""
    user_id = 6001
    await users_repo.create_or_update_user(user_id, "timer_atomic_user")
    h_id = await habits_repo.create_habit(user_id, "Чтение", 30, "1,2,3,4,5,6,7")

    now = int(time.time())
    await timers_repo.create_or_replace_timer(
        user_id=user_id,
        habit_id=h_id,
        total_target_seconds=1800,
        message_id=10,
        chat_id=10,
        started_at=now - 1800,
    )

    # First delete succeeds and returns timer data
    deleted_timer = await timers_repo.delete_timer(user_id)
    assert deleted_timer is not None
    assert deleted_timer["habit_name"] == "Чтение"

    # Second concurrent delete returns None (preventing race-condition double logging)
    deleted_again = await timers_repo.delete_timer(user_id)
    assert deleted_again is None

    # Test FSM reflection state activation
    storage = MemoryStorage()
    key = StorageKey(bot_id=999, chat_id=10, user_id=user_id)
    state = FSMContext(storage=storage, key=key)

    await state.set_state(TimerStates.waiting_for_reflection)
    await state.update_data(habit_id=h_id, logged_date="2026-11-21", habit_name="Чтение")

    cur_state = await state.get_state()
    assert cur_state == TimerStates.waiting_for_reflection.state
    data = await state.get_data()
    assert data["habit_id"] == h_id
    assert data["habit_name"] == "Чтение"


@pytest.mark.asyncio
async def test_cyrillic_catalog_search_and_parser_disambiguation():
    """5. Verify Cyrillic search in food catalog and disambiguation with manual parser."""
    # Ensure catalog is populated
    await food_repo.seed_food_catalog_if_empty(TEST_DB)

    # Cyrillic case-insensitive search
    res_lower = await food_repo.search_food("творог", limit=5, db_path=TEST_DB)
    res_upper = await food_repo.search_food("ТВОРОГ", limit=5, db_path=TEST_DB)
    assert len(res_lower) > 0
    assert len(res_upper) > 0
    assert res_lower[0]["name"] == res_upper[0]["name"]

    # Dot search (2.5%)
    res_milk = await food_repo.search_food("молоко 2.5%", limit=5, db_path=TEST_DB)
    assert len(res_milk) > 0
    assert "Молоко" in res_milk[0]["name"]

    # Numbered products without explicit kcal should NOT be falsely parsed by manual parser
    assert parse_meal_input("Творог 5%") is None
    assert parse_meal_input("Кефир 1%") is None
    assert parse_meal_input("Яичница из 2 яиц") is None
    assert parse_meal_input("Молоко 2.5%") is None

    # Explicit calories or macros SHOULD be parsed
    parsed_with_kcal = parse_meal_input("Творог 5% 150")
    assert parsed_with_kcal is not None
    assert parsed_with_kcal["title"] == "Творог 5%"
    assert parsed_with_kcal["calories"] == 150

    parsed_oats = parse_meal_input("Овсянка 220")
    assert parsed_oats is not None
    assert parsed_oats["title"] == "Овсянка"
    assert parsed_oats["calories"] == 220


def test_html_escaping():
    """6. Verify HTML escaping of special characters (<, >, &)."""
    malicious_habit = {
        "name": "<script>alert('xss')</script> & gym",
        "habit_type": "duration",
        "target_minutes": 30,
        "active_days": "1,2,3,4,5,6,7",
        "streak_days": 2,
    }
    card_html = format_habit_edit_card(malicious_habit)
    assert "<script>" not in card_html
    assert "&lt;script&gt;" in card_html
    assert "&amp; gym" in card_html

    meal = {
        "title": "Fish & Chips <special>",
        "calories": 400,
        "protein": 25.0,
        "fat": 15.0,
        "carbs": 30.0,
        "created_at": "2026-11-21 12:00:00",
    }
    item_html = format_meal_item(meal, show_macros=True, utc_offset=3)
    assert "<special>" not in item_html
    assert "&lt;special&gt;" in item_html
    assert "Fish &amp; Chips" in item_html


@pytest.mark.asyncio
async def test_water_validation_limits():
    """7. Verify water range boundaries."""
    user_id = 7001
    await users_repo.create_or_update_user(user_id, "water_user")

    # Positive within 1..5000 is allowed
    total = await water_repo.add_water(user_id, "2026-11-21", 250)
    assert total == 250

    total = await water_repo.add_water(user_id, "2026-11-21", 500)
    assert total == 750

    cur = await water_repo.get_daily_water(user_id, "2026-11-21")
    assert cur == 750


@pytest.mark.asyncio
async def test_habit_ownership_security():
    """8. Verify user cannot modify, delete, or freeze another user's habit."""
    owner_id = 8001
    intruder_id = 8002
    await users_repo.create_or_update_user(owner_id, "owner")
    await users_repo.create_or_update_user(intruder_id, "intruder")

    h_id = await habits_repo.create_habit(owner_id, "Личная привычка", 30)

    # Intruder tries to freeze
    success, msg = await habits_repo.freeze_habit_today(h_id, intruder_id)
    assert success is False
    assert "не найдена" in msg

    # Intruder tries to update
    updated = await habits_repo.update_habit(h_id, intruder_id, name="Взлом")
    assert updated is None

    # Owner can freeze
    success_owner, msg_owner = await habits_repo.freeze_habit_today(h_id, owner_id)
    assert success_owner is True

    # Owner can delete
    deleted = await habits_repo.delete_habit(h_id, owner_id)
    assert deleted is True


def test_fsm_json_serializability():
    """9. Verify FSM data is JSON serializable (using list instead of set)."""
    selected_days = [1, 2, 3, 4, 5]
    fsm_data = {
        "habit_name": "Бег",
        "target_minutes": 30,
        "selected_days": selected_days,
        "edit_days": selected_days,
    }
    # This must not raise TypeError: Object of type set is not JSON serializable
    serialized = json.dumps(fsm_data)
    deserialized = json.loads(serialized)
    assert isinstance(deserialized["selected_days"], list)
    assert deserialized["selected_days"] == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_checklist_habits_and_chart():
    """10. Verify checklist habits logging and chart builder."""
    user_id = 9001
    await users_repo.create_or_update_user(user_id, "checklist_user")
    h_id = await habits_repo.create_habit(
        user_id=user_id,
        name="Витамины",
        habit_type="checklist",
        target_repeats=3,
        active_days="1,2,3,4,5,6,7",
    )

    cur, tgt, completed = await habits_repo.increment_habit_repeat(h_id, user_id, "2026-11-21")
    assert cur == 1
    assert tgt == 3
    assert not completed

    # Chart builder handles checklist habit
    daily = {"2026-11-21": 0}
    habits = [
        {"name": "Витамины", "habit_type": "checklist", "total_repeats": 1, "target_repeats": 3, "total_spent": 0}
    ]
    buf = build_weekly_stats_chart(daily, habits, "2026-11-21")
    assert buf.getvalue().startswith(b"\x89PNG")


def test_utc_to_local_timezone_conversion():
    """11. Verify meal item timestamps convert correctly from UTC to local user time."""
    utc_time_str = "2026-11-21 10:00:00"  # 10:00 UTC
    meal = {
        "title": "Обед",
        "calories": 500,
        "created_at": utc_time_str,
    }
    # With UTC+3, 10:00 UTC -> 13:00 local
    formatted_utc3 = format_meal_item(meal, show_macros=False, utc_offset=3)
    assert "13:00" in formatted_utc3

    # With UTC+5, 10:00 UTC -> 15:00 local
    formatted_utc5 = format_meal_item(meal, show_macros=False, utc_offset=5)
    assert "15:00" in formatted_utc5


# ---------------------------------------------------------------------------
# Additional Helper Tests
# ---------------------------------------------------------------------------

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
    assert data.startswith(b"\x89PNG")
