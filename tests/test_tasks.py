"""Automated tests for Todoist-style Task Tracker, subtasks, lists, timers, reminders, and API."""

import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from aiogram import Bot
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.config import settings
from bot.database import habits_repo, tasks_repo, timers_repo, users_repo
from bot.database.db import get_db, init_db
from bot.services.task_parser import parse_quick_task
from bot.web.server import create_web_app

TEST_BOT_TOKEN = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789"
USER_A = 111000111
USER_B = 222000222


def make_auth_header(
    user_id: int,
    bot_token: str = TEST_BOT_TOKEN,
    auth_date: Optional[int] = None,
) -> Dict[str, str]:
    """Build X-Telegram-Init-Data header with valid HMAC signature."""
    if auth_date is None:
        auth_date = int(time.time())

    user_dict = {
        "id": user_id,
        "first_name": f"User_{user_id}",
        "username": f"user_{user_id}",
    }
    data = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user_dict, separators=(",", ":")),
    }
    sorted_pairs = sorted(data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_pairs)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    params = dict(data)
    params["hash"] = sig
    return {"X-Telegram-Init-Data": urllib.parse.urlencode(params)}


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    """Set up isolated SQLite database for each test."""
    db_file = tmp_path / "test_tasks.db"
    monkeypatch.setattr(settings, "DB_PATH", str(db_file))
    await init_db(str(db_file))
    yield


@pytest.fixture
def mock_bot():
    """Mock aiogram Bot instance."""
    bot = MagicMock(spec=Bot)
    bot.id = 123456789
    bot.edit_message_text = AsyncMock(return_value=True)
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=101))
    return bot


@pytest.fixture
async def web_client(mock_bot):
    """aiohttp TestClient fixture."""
    app = create_web_app(mock_bot, bot_token=TEST_BOT_TOKEN)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()


# ===========================================================================
# 1. Database Schema Migration Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_active_timers_legacy_db_migration(tmp_path):
    """Verify that a legacy database with habit-only active_timers migrates safely."""
    legacy_db_path = tmp_path / "legacy_timer.db"

    # Create old schema
    async with aiosqlite.connect(legacy_db_path) as db:
        await db.execute("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                utc_offset INTEGER NOT NULL DEFAULT 3,
                checkin_time TEXT NOT NULL DEFAULT '21:00'
            );
        """)
        await db.execute("""
            CREATE TABLE habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                habit_type TEXT NOT NULL DEFAULT 'duration',
                target_minutes INTEGER NOT NULL DEFAULT 30
            );
        """)
        await db.execute("""
            CREATE TABLE active_timers (
                user_id INTEGER PRIMARY KEY,
                habit_id INTEGER NOT NULL,
                started_at INTEGER NOT NULL,
                total_target_seconds INTEGER NOT NULL,
                elapsed_before_pause INTEGER NOT NULL DEFAULT 0,
                is_paused INTEGER NOT NULL DEFAULT 0,
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );
        """)
        await db.commit()

    # Run init_db which should perform table swap migration
    await init_db(str(legacy_db_path))

    # Verify column structure in active_timers
    async with aiosqlite.connect(legacy_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("PRAGMA table_info(active_timers);") as cursor:
            cols = {r["name"]: dict(r) for r in await cursor.fetchall()}
            assert "task_id" in cols
            assert "habit_id" in cols
            # habit_id should not be NOT NULL anymore (notnull == 0)
            assert cols["habit_id"]["notnull"] == 0


# ===========================================================================
# 2. Task Lists & Isolation Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_task_lists_and_atomic_deletion():
    """Verify Inbox creation, custom list creation, and atomic task reassignment on deletion."""
    await users_repo.create_or_update_user(USER_A, "user_a")
    await users_repo.create_or_update_user(USER_B, "user_b")

    # 1. Automatic Inbox list creation
    inbox_a = await tasks_repo.ensure_inbox_list(USER_A)
    assert inbox_a["is_inbox"] == 1
    assert inbox_a["name"] == "Входящие"

    # User B has own isolated Inbox
    inbox_b = await tasks_repo.ensure_inbox_list(USER_B)
    assert inbox_b["id"] != inbox_a["id"]

    # 2. Custom list creation
    work_list_id = await tasks_repo.create_list(USER_A, "Работа", icon="💼")
    lists_a = await tasks_repo.get_lists(USER_A)
    assert len(lists_a) == 2  # Inbox + Работа

    # 3. Add task to custom list
    task_id = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=work_list_id,
        title="Отчет по спринту",
    )
    t = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert t["list_id"] == work_list_id

    # 4. Attempt to delete Inbox (must be disallowed)
    del_inbox_ok = await tasks_repo.delete_list(inbox_a["id"], USER_A)
    assert del_inbox_ok is False

    # 5. Delete custom list -> task atomically moved to Inbox
    del_work_ok = await tasks_repo.delete_list(work_list_id, USER_A)
    assert del_work_ok is True

    t_after = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert t_after["list_id"] == inbox_a["id"]


# ===========================================================================
# 3. Subtasks Tests (Strictly 1 Level Deep)
# ===========================================================================

@pytest.mark.asyncio
async def test_subtasks_crud_and_progress():
    """Test 1-level subtask creation, toggle, update, and deletion."""
    await users_repo.create_or_update_user(USER_A, "user_a")
    inbox = await tasks_repo.ensure_inbox_list(USER_A)

    task_id = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Запуск проекта",
    )

    s1 = await tasks_repo.create_subtask(task_id, USER_A, "Написать тесты")
    s2 = await tasks_repo.create_subtask(task_id, USER_A, "Собрать билд")

    t = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert len(t["subtasks"]) == 2
    assert t["subtasks"][0]["title"] == "Написать тесты"
    assert t["subtasks"][0]["is_completed"] == 0

    # Toggle subtask
    await tasks_repo.toggle_subtask(s1, USER_A, is_completed=True)
    t = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert t["subtasks"][0]["is_completed"] == 1

    # Delete subtask
    await tasks_repo.delete_subtask(s2, USER_A)
    t = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert len(t["subtasks"]) == 1


# ===========================================================================
# 4. Smart Today View & Overdue Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_smart_today_view():
    """Verify Smart Today aggregates overdue, today, and completed today."""
    await users_repo.create_or_update_user(USER_A, "user_a")
    inbox = await tasks_repo.ensure_inbox_list(USER_A)
    local_today = "2026-11-22"
    yesterday = "2026-11-21"
    tomorrow = "2026-11-23"

    # Task 1: Overdue uncompleted
    t1 = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Вчерашняя задача",
        due_date=yesterday,
    )

    # Task 2: Due today
    t2 = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Сегодняшняя задача",
        due_date=local_today,
    )

    # Task 3: Future task (tomorrow)
    t3 = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Завтрашняя задача",
        due_date=tomorrow,
    )

    # Task 4: Completed today
    t4 = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Сделано сегодня",
        due_date=local_today,
    )
    await tasks_repo.toggle_task_complete(t4, USER_A, local_today)

    today_view = await tasks_repo.get_today_tasks(USER_A, local_today)
    today_ids = [t["id"] for t in today_view]

    assert t1 in today_ids
    assert t2 in today_ids
    assert t4 in today_ids
    assert t3 not in today_ids  # Future task must not clutter today

    overdue_task = next(t for t in today_view if t["id"] == t1)
    assert overdue_task.get("is_overdue") is True


# ===========================================================================
# 5. Recurrence Rollover Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_recurrence_rollover():
    """Completing a recurring task creates an occurrence snapshot and advances due_date."""
    await users_repo.create_or_update_user(USER_A, "user_a")
    inbox = await tasks_repo.ensure_inbox_list(USER_A)
    local_today = "2026-11-22"

    task_id = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Полить цветы",
        due_date=local_today,
        recurrence="daily",
        estimated_minutes=15,
    )
    # Add a subtask
    await tasks_repo.create_subtask(task_id, USER_A, "Набрать воду")

    # Complete recurring task
    new_state = await tasks_repo.toggle_task_complete(task_id, USER_A, local_today)
    assert new_state["is_completed"] == 0  # Still active because it rolled over!
    assert new_state["rolled_over"] is True

    # Active task should now have due_date == tomorrow
    active_t = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert active_t["due_date"] == "2026-11-23"
    assert active_t["is_completed"] == 0
    assert active_t["subtasks"][0]["is_completed"] == 0

    # History snapshot was created for local_today
    completed_range = await tasks_repo.get_completed_tasks_in_range(USER_A, "2026-11-20", "2026-11-25")
    snapshot = next((t for t in completed_range if t["title"] == "Полить цветы"), None)
    assert snapshot is not None
    assert snapshot["completed_at"] == local_today
    assert snapshot["is_completed"] == 1


# ===========================================================================
# 6. Quick Task Parser Tests
# ===========================================================================

def test_parse_quick_task_variations():
    """Verify task string parser with dates, times, durations, recurrence, and lists."""
    base_date = "2026-11-22"

    # Full example
    p1 = parse_quick_task("Позвонить врачу завтра 14:30 #здоровье 25m", base_date)
    assert p1 is not None
    assert p1["title"] == "Позвонить врачу"
    assert p1["due_date"] == "2026-11-23"
    assert p1["due_time"] == "14:30"
    assert p1["list_name"] == "здоровье"
    assert p1["estimated_minutes"] == 25
    assert p1["recurrence"] == "none"

    # Recurrence & hour estimate
    p2 = parse_quick_task("Оплата сервера каждый день 1.5ч #работа", base_date)
    assert p2 is not None
    assert p2["title"] == "Оплата сервера"
    assert p2["due_date"] == base_date
    assert p2["recurrence"] == "daily"
    assert p2["estimated_minutes"] == 90
    assert p2["list_name"] == "работа"

    # Weekdays & Russian time format
    p3 = parse_quick_task("Зарядка по будням 15мин", base_date)
    assert p3 is not None
    assert p3["title"] == "Зарядка"
    assert p3["recurrence"] == "weekdays"
    assert p3["estimated_minutes"] == 15

    # Empty title returns None
    assert parse_quick_task("#дом 15m завтра", base_date) is None


# ===========================================================================
# 7. Focus Timer for Tasks Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_focus_timer_task_integration():
    """Test timer start, XOR constraint, spent minutes accumulation, and stop for tasks."""
    await users_repo.create_or_update_user(USER_A, "user_a")
    inbox = await tasks_repo.ensure_inbox_list(USER_A)

    task_id = await tasks_repo.create_task(
        user_id=USER_A,
        list_id=inbox["id"],
        title="Кодинг фичи",
        estimated_minutes=25,
    )

    # 1. Enforces XOR constraint
    with pytest.raises(ValueError):
        await timers_repo.create_or_replace_timer(
            user_id=USER_A,
            habit_id=None,
            task_id=None,
        )

    # 2. Start timer for task
    await timers_repo.create_or_replace_timer(
        user_id=USER_A,
        task_id=task_id,
        total_target_seconds=1500,
    )

    t = await timers_repo.get_timer(USER_A)
    assert t is not None
    assert t["target_type"] == "task"
    assert t["task_id"] == task_id
    assert t["target_name"] == "Кодинг фичи"

    # 3. Add spent minutes
    await tasks_repo.add_task_spent_minutes(task_id, 25)
    task_after = await tasks_repo.get_task_by_id(task_id, USER_A)
    assert task_after["spent_minutes"] == 25


# ===========================================================================
# 8. Web REST API Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_web_api_task_tracker_flow(web_client):
    """Full lifecycle test of task tracker via REST API."""
    headers = make_auth_header(USER_A)

    # 1. Create custom task list
    r_list = await web_client.post(
        "/api/task-lists",
        headers=headers,
        json={"name": "Проект X", "icon": "🚀"},
    )
    assert r_list.status == 200
    list_data = (await r_list.json())["data"]
    list_id = list_data["id"]

    # 2. Create task with subtasks
    r_task = await web_client.post(
        "/api/tasks",
        headers=headers,
        json={
            "title": "Архитектура модуля",
            "list_id": list_id,
            "due_date": "2026-11-22",
            "estimated_minutes": 45,
            "subtasks": ["Диаграмма", "Интерфейсы"],
        },
    )
    assert r_task.status == 200
    t_res = (await r_task.json())["data"]
    task_id = t_res["id"]
    assert len(t_res["subtasks"]) == 2

    # 3. GET /api/tasks
    r_get = await web_client.get("/api/tasks?view=today", headers=headers)
    assert r_get.status == 200
    get_data = (await r_get.json())["data"]
    assert len(get_data["tasks"]) >= 1

    # 4. Toggle subtask
    sub_id = t_res["subtasks"][0]["id"]
    r_sub = await web_client.patch(
        f"/api/subtasks/{sub_id}",
        headers=headers,
        json={"is_completed": True},
    )
    assert r_sub.status == 200

    # 5. Start focus timer for task
    r_timer_start = await web_client.post(
        "/api/timer/start",
        headers=headers,
        json={"task_id": task_id, "duration_minutes": 25},
    )
    assert r_timer_start.status == 201

    # 6. Stop focus timer
    r_timer_stop = await web_client.post("/api/timer/stop", headers=headers)
    assert r_timer_stop.status == 200
    stop_data = (await r_timer_stop.json())["data"]
    assert stop_data["target_type"] == "task"
    assert stop_data["task_id"] == task_id

    # 7. Toggle task complete
    r_toggle = await web_client.post(f"/api/tasks/{task_id}/toggle", headers=headers)
    assert r_toggle.status == 200
    assert (await r_toggle.json())["is_completed"] is True
