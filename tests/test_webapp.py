"""Automated tests for Telegram Mini App HTML Widget, HMAC Authentication, API endpoints, and Chat Synchronization."""

import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.config import settings
from bot.database import habits_repo, nutrition_repo, users_repo, water_repo, weight_repo, widgets_repo
from bot.database.db import get_db, init_db
from bot.web.auth import InitDataAuthError, validate_telegram_init_data
from bot.web.server import create_web_app

TEST_BOT_TOKEN = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789"
TEST_USER_ID = 888123456
TEST_USER_ID_2 = 999876543


def make_test_init_data(
    user_id: int,
    bot_token: str = TEST_BOT_TOKEN,
    auth_date: Optional[int] = None,
    username: str = "widget_tester",
) -> str:
    """Generate cryptographically valid Telegram WebApp initData string."""
    if auth_date is None:
        auth_date = int(time.time())

    user_dict = {
        "id": user_id,
        "first_name": "WidgetTester",
        "last_name": "Bot",
        "username": username,
        "language_code": "ru",
    }
    user_json = json.dumps(user_dict, separators=(",", ":"))

    data = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_json,
    }

    sorted_pairs = sorted(data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_pairs)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    params = dict(data)
    params["hash"] = sig
    return urllib.parse.urlencode(params)


@pytest.fixture(autouse=True)
async def setup_test_database(tmp_path, monkeypatch):
    """Set up temporary SQLite database for web tests."""
    db_file = tmp_path / "test_widget.db"
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
    """aiohttp TestClient fixture configured with mock bot."""
    app = create_web_app(mock_bot, bot_token=TEST_BOT_TOKEN)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()


# ===========================================================================
# 1. Tests for validate_telegram_init_data (HMAC-SHA256)
# ===========================================================================

def test_validate_init_data_success():
    """Valid signed initData passes and parses user id correctly."""
    init_data = make_test_init_data(user_id=TEST_USER_ID, bot_token=TEST_BOT_TOKEN)
    res = validate_telegram_init_data(init_data, bot_token=TEST_BOT_TOKEN)
    assert res["user"]["id"] == TEST_USER_ID
    assert res["user"]["username"] == "widget_tester"
    assert "auth_date" in res


def test_validate_init_data_tampered():
    """Tampering with user data without re-signing fails with InitDataAuthError."""
    init_data = make_test_init_data(user_id=TEST_USER_ID, bot_token=TEST_BOT_TOKEN)
    # Tamper by replacing user ID
    tampered = init_data.replace(str(TEST_USER_ID), "111222333")
    with pytest.raises(InitDataAuthError):
        validate_telegram_init_data(tampered, bot_token=TEST_BOT_TOKEN)


def test_validate_init_data_bad_token():
    """Signature generated with wrong bot token fails verification."""
    init_data = make_test_init_data(user_id=TEST_USER_ID, bot_token=TEST_BOT_TOKEN)
    wrong_token = "987654321:XYZotherBotTokenWrongKey1234567890"
    with pytest.raises(InitDataAuthError):
        validate_telegram_init_data(init_data, bot_token=wrong_token)


def test_validate_init_data_expired():
    """Expired auth_date exceeds max_age_seconds and fails verification."""
    old_ts = int(time.time()) - 90000  # > 24 hours ago
    init_data = make_test_init_data(user_id=TEST_USER_ID, bot_token=TEST_BOT_TOKEN, auth_date=old_ts)
    with pytest.raises(InitDataAuthError):
        validate_telegram_init_data(init_data, bot_token=TEST_BOT_TOKEN, max_age_seconds=86400)


# ===========================================================================
# 2. Tests for Static Assets and Path Traversal Protection
# ===========================================================================

async def test_static_routes_and_security(web_client: TestClient):
    """Verify health check, HTML shell, CSS/JS assets, and path traversal block."""
    # Health check
    resp = await web_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"

    # Widget HTML shell
    resp_widget = await web_client.get("/widget")
    assert resp_widget.status == 200
    assert resp_widget.content_type == "text/html"
    text = await resp_widget.text()
    assert "telegram-web-app.js" in text
    assert "/widget/assets/app.js" in text

    # CSS Asset
    resp_css = await web_client.get("/widget/assets/styles.css")
    assert resp_css.status == 200
    assert resp_css.content_type == "text/css"
    css_text = await resp_css.text()
    assert "--tg-theme-bg-color" in css_text
    assert "--tg-theme-secondary-bg-color" in css_text
    assert "--tg-theme-text-color" in css_text

    # JS Asset
    resp_js = await web_client.get("/widget/assets/app.js")
    assert resp_js.status == 200
    assert resp_js.content_type == "application/javascript"
    js_text = await resp_js.text()
    assert "Telegram" in js_text
    assert "X-Telegram-Init-Data" in js_text

    # Path traversal attack attempts
    resp_attack1 = await web_client.get("/widget/assets/../../bot/config.py")
    assert resp_attack1.status in (403, 404)

    resp_attack2 = await web_client.get("/widget/assets/nonexistent.png")
    assert resp_attack2.status in (403, 404)


# ===========================================================================
# 3. Tests for API Middleware Authentication
# ===========================================================================

async def test_missing_init_data_header_401(web_client: TestClient):
    """API endpoints without X-Telegram-Init-Data return HTTP 401."""
    resp = await web_client.get("/api/bootstrap")
    assert resp.status == 401
    data = await resp.json()
    assert data["ok"] is False
    assert "auth" in data["error"].lower() or "missing" in data["error"].lower()


async def test_dev_preview_mode_allows_access(web_client: TestClient):
    """Dev preview mode with ?dev=1 or X-Dev-Preview: 1 allows browser access without initData."""
    resp1 = await web_client.get("/api/bootstrap?dev=1")
    assert resp1.status == 200
    data1 = await resp1.json()
    assert data1["ok"] is True
    assert "user" in data1["data"]

    resp2 = await web_client.get("/api/bootstrap", headers={"X-Dev-Preview": "1"})
    assert resp2.status == 200
    data2 = await resp2.json()
    assert data2["ok"] is True


async def test_invalid_init_data_header_401(web_client: TestClient):
    """API endpoints with malformed initData return HTTP 401."""
    resp = await web_client.get(
        "/api/bootstrap",
        headers={"X-Telegram-Init-Data": "bad_query_string&hash=deadbeef"},
    )
    assert resp.status == 401
    data = await resp.json()
    assert data["ok"] is False


# ===========================================================================
# 4. Tests for Bootstrap and Settings API
# ===========================================================================

async def test_api_bootstrap_and_settings(web_client: TestClient):
    """Bootstrap returns full structure; settings update timezone and notifications."""
    valid_header = make_test_init_data(TEST_USER_ID)
    headers = {"X-Telegram-Init-Data": valid_header}

    # Bootstrap
    resp = await web_client.get("/api/bootstrap", headers=headers)
    assert resp.status == 200
    res = await resp.json()
    assert res["ok"] is True
    data = res["data"]
    assert data["user"]["user_id"] == TEST_USER_ID
    assert "local_date" in data
    assert "habits_today" in data
    assert "nutrition" in data
    assert "water" in data
    assert "stats_7d" in data

    # Patch settings
    patch_resp = await web_client.patch(
        "/api/settings",
        headers=headers,
        json={
            "utc_offset": 5,
            "checkin_time": "22:15",
            "weight_reminder_enabled": True,
            "weight_reminder_time": "07:45",
            "mute_action": "forever",
        },
    )
    assert patch_resp.status == 200
    patch_data = await patch_resp.json()
    assert patch_data["ok"] is True
    assert patch_data["data"]["utc_offset"] == 5
    assert patch_data["data"]["checkin_time"] == "22:15"
    assert patch_data["data"]["weight_reminder_enabled"] == 1
    assert patch_data["data"]["weight_reminder_time"] == "07:45"
    assert patch_data["data"]["notifications_muted_until"] == 2147483647


async def test_api_test_notification(web_client: TestClient):
    """Test POST /api/settings/test-notification endpoint."""
    valid_header = make_test_init_data(TEST_USER_ID)
    headers = {"X-Telegram-Init-Data": valid_header}

    resp = await web_client.post("/api/settings/test-notification", headers=headers)
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert "local_time" in data
    assert "local_date" in data
    assert "utc_offset" in data


async def test_api_stats_periods(web_client: TestClient):
    """Test /api/stats endpoint supports 7, 15, 30, and 90 day periods."""
    valid_header = make_test_init_data(TEST_USER_ID)
    headers = {"X-Telegram-Init-Data": valid_header}

    for period in (7, 15, 30, 90):
        resp = await web_client.get(f"/api/stats?period={period}", headers=headers)
        assert resp.status == 200
        res = await resp.json()
        assert res["ok"] is True
        data = res["data"]
        assert data["period"] == period
        assert len(data["dates"]) == period
        assert "daily_focus" in data
        assert "daily_completed" in data
        assert "daily_calories" in data
        assert "daily_water" in data
        assert "daily_weight" in data


# ===========================================================================
# 5. Tests for Habits CRUD, Check, and Freeze Lifecycle
# ===========================================================================

async def test_api_habits_lifecycle_and_security(web_client: TestClient):
    """Habits creation, check, freeze, patch, delete, and cross-user ownership isolation."""
    user1_headers = {"X-Telegram-Init-Data": make_test_init_data(TEST_USER_ID)}
    user2_headers = {"X-Telegram-Init-Data": make_test_init_data(TEST_USER_ID_2)}

    # 1. Create duration habit
    create_dur_resp = await web_client.post(
        "/api/habits",
        headers=user1_headers,
        json={
            "name": "Чтение книги",
            "habit_type": "duration",
            "target_minutes": 45,
            "active_days": "1,2,3,4,5,6,7",
        },
    )
    assert create_dur_resp.status == 201
    dur_data = await create_dur_resp.json()
    dur_id = dur_data["data"]["habit_id"]

    # 2. Create checklist habit
    create_chk_resp = await web_client.post(
        "/api/habits",
        headers=user1_headers,
        json={
            "name": "Витамины",
            "habit_type": "checklist",
            "target_repeats": 2,
            "active_days": "1,2,3,4,5",
        },
    )
    assert create_chk_resp.status == 201
    chk_id = (await create_chk_resp.json())["data"]["habit_id"]

    # 3. Check habit (+1 repeat)
    check_resp = await web_client.post(f"/api/habits/{chk_id}/check", headers=user1_headers)
    assert check_resp.status == 200
    chk_res = await check_resp.json()
    assert chk_res["data"]["current_repeats"] == 1
    assert chk_res["data"]["is_completed"] is False

    # Second check -> completed
    check_resp2 = await web_client.post(f"/api/habits/{chk_id}/check", headers=user1_headers)
    assert check_resp2.status == 200
    chk_res2 = await check_resp2.json()
    assert chk_res2["data"]["current_repeats"] == 2
    assert chk_res2["data"]["is_completed"] is True

    # 4. Freeze duration habit
    freeze_resp = await web_client.post(f"/api/habits/{dur_id}/freeze", headers=user1_headers)
    assert freeze_resp.status == 200
    assert (await freeze_resp.json())["ok"] is True

    # 5. Cross-user isolation: User 2 cannot patch or delete User 1's habit
    unauth_patch = await web_client.patch(
        f"/api/habits/{dur_id}",
        headers=user2_headers,
        json={"name": "Hacked name"},
    )
    assert unauth_patch.status == 404

    unauth_delete = await web_client.delete(f"/api/habits/{dur_id}", headers=user2_headers)
    assert unauth_delete.status == 404

    # 6. User 1 patches habit
    patch_resp = await web_client.patch(
        f"/api/habits/{dur_id}",
        headers=user1_headers,
        json={"name": "Чтение классики", "target_minutes": 60},
    )
    assert patch_resp.status == 200
    patch_data = await patch_resp.json()
    assert patch_data["data"]["name"] == "Чтение классики"
    assert patch_data["data"]["target_minutes"] == 60

    # 7. User 1 deletes habit
    del_resp = await web_client.delete(f"/api/habits/{dur_id}", headers=user1_headers)
    assert del_resp.status == 200
    assert (await del_resp.json())["ok"] is True


# ===========================================================================
# 6. Tests for Focus Timer Session Lifecycle
# ===========================================================================

async def test_api_timer_lifecycle(web_client: TestClient):
    """Timer start, duplicate conflict 409, pause, resume, add-10, stop, and reflection note."""
    headers = {"X-Telegram-Init-Data": make_test_init_data(TEST_USER_ID)}

    # Create habit for timer
    create_h = await web_client.post(
        "/api/habits",
        headers=headers,
        json={"name": "Программирование", "habit_type": "duration", "target_minutes": 30},
    )
    hid = (await create_h.json())["data"]["habit_id"]

    # 1. Start timer
    start_resp = await web_client.post(
        "/api/timer/start",
        headers=headers,
        json={"habit_id": hid},
    )
    assert start_resp.status == 201
    timer = (await start_resp.json())["data"]
    assert timer["habit_id"] == hid
    assert timer["total_target_seconds"] == 1800

    # 2. Start second timer while first is running -> 409 Conflict
    conflict_resp = await web_client.post(
        "/api/timer/start",
        headers=headers,
        json={"habit_id": hid},
    )
    assert conflict_resp.status == 409

    # 3. Pause
    pause_resp = await web_client.post("/api/timer/pause", headers=headers)
    assert pause_resp.status == 200
    assert (await pause_resp.json())["data"]["is_paused"] == 1

    # 4. Resume
    resume_resp = await web_client.post("/api/timer/resume", headers=headers)
    assert resume_resp.status == 200
    assert (await resume_resp.json())["data"]["is_paused"] == 0

    # 5. Add 10 minutes (+600 seconds)
    add_resp = await web_client.post("/api/timer/add-10", headers=headers)
    assert add_resp.status == 200
    assert (await add_resp.json())["data"]["total_target_seconds"] == 2400

    # 6. Stop timer
    stop_resp = await web_client.post("/api/timer/stop", headers=headers)
    assert stop_resp.status == 200
    summary = (await stop_resp.json())["data"]
    assert summary["habit_id"] == hid
    assert "actual_minutes" in summary
    assert "spent_today" in summary

    # 7. Reflection note
    ref_resp = await web_client.post(
        "/api/timer/reflection",
        headers=headers,
        json={
            "habit_id": hid,
            "logged_date": summary["logged_date"],
            "note": "Успешно разобрал алгоритмы",
        },
    )
    assert ref_resp.status == 200
    assert (await ref_resp.json())["ok"] is True


# ===========================================================================
# 7. Tests for Health: Nutrition, Water, and Weight Tracking
# ===========================================================================

async def test_api_health_lifecycle(web_client: TestClient):
    """Food catalog search, 4 meal modes, water add/undo, weight logging, and goals."""
    headers = {"X-Telegram-Init-Data": make_test_init_data(TEST_USER_ID)}

    # 1. Food search
    search_resp = await web_client.get("/api/food/search?q=сыр", headers=headers)
    assert search_resp.status == 200
    foods = (await search_resp.json())["data"]
    assert len(foods) > 0
    food_item = foods[0]

    # 2. Add meal via catalog mode
    cat_resp = await web_client.post(
        "/api/meals",
        headers=headers,
        json={
            "mode": "catalog",
            "food_id": food_item["id"],
            "grams": 150,
            "meal_type": "breakfast",
        },
    )
    assert cat_resp.status == 201
    meal_cat = (await cat_resp.json())["data"]
    assert meal_cat["calories"] > 0
    meal_id = meal_cat["id"]

    # 3. Add meal via repeat mode
    rep_resp = await web_client.post(
        "/api/meals",
        headers=headers,
        json={"mode": "repeat", "meal_id": meal_id},
    )
    assert rep_resp.status == 201
    rep_data = (await rep_resp.json())["data"]
    assert rep_data["title"] == meal_cat["title"]

    # 4. Add meal via quick_text mode
    quick_resp = await web_client.post(
        "/api/meals",
        headers=headers,
        json={"mode": "quick_text", "text": "Творог 150 20/4/3", "meal_type": "snack"},
    )
    assert quick_resp.status == 201
    q_data = (await quick_resp.json())["data"]
    assert q_data["calories"] == 150
    assert q_data["protein"] == 20.0

    # 5. Add meal via manual mode
    man_resp = await web_client.post(
        "/api/meals",
        headers=headers,
        json={
            "mode": "manual",
            "title": "Смузи протеиновый",
            "calories": 240,
            "protein": 25.0,
            "fat": 3.0,
            "carbs": 15.0,
            "meal_type": "snack",
        },
    )
    assert man_resp.status == 201

    # 6. Delete meal
    del_resp = await web_client.delete(f"/api/meals/{meal_id}", headers=headers)
    assert del_resp.status == 200

    # 7. Water logging
    water_resp = await web_client.post(
        "/api/water",
        headers=headers,
        json={"amount_ml": 350},
    )
    assert water_resp.status == 200
    assert (await water_resp.json())["data"]["current_ml"] == 350

    # Invalid water
    inv_water = await web_client.post("/api/water", headers=headers, json={"amount_ml": -100})
    assert inv_water.status == 400

    # Water undo
    undo_resp = await web_client.post("/api/water/undo", headers=headers)
    assert undo_resp.status == 200
    assert (await undo_resp.json())["data"]["current_ml"] == 0

    # 8. Weight logging
    weight_resp = await web_client.post(
        "/api/weight",
        headers=headers,
        json={"weight": 72.8, "note": "После тренировки"},
    )
    assert weight_resp.status == 200
    assert (await weight_resp.json())["data"]["weight"] == 72.8

    inv_weight = await web_client.post("/api/weight", headers=headers, json={"weight": 500.0})
    assert inv_weight.status == 400

    # 9. Nutrition goals update
    goals_resp = await web_client.patch(
        "/api/nutrition/goals",
        headers=headers,
        json={
            "target_calories": 2400,
            "target_protein": 140.0,
            "target_fat": 65.0,
            "target_carbs": 250.0,
            "track_bju": True,
            "track_water": True,
            "water_target_ml": 2500,
        },
    )
    assert goals_resp.status == 200
    goals = (await goals_resp.json())["data"]
    assert goals["target_calories"] == 2400
    assert goals["track_bju"] == 1


# ===========================================================================
# 8. Tests for Real-Time Chat Message Synchronization
# ===========================================================================

async def test_chat_message_sync(web_client: TestClient, mock_bot: MagicMock):
    """Verify editing linked chat card and handling 'message is not modified' gracefully."""
    test_wid = "wid_sync_test_001"
    headers = {
        "X-Telegram-Init-Data": make_test_init_data(TEST_USER_ID),
        "X-Widget-Id": test_wid,
    }

    # Ensure user exists in db for foreign key constraint
    await users_repo.create_or_update_user(user_id=TEST_USER_ID, username="widget_tester")

    # Save widget message link
    await widgets_repo.save_widget_message(
        wid=test_wid,
        user_id=TEST_USER_ID,
        chat_id=1001,
        message_id=2002,
        view_type="habits",
    )

    # Create habit
    c_resp = await web_client.post(
        "/api/habits",
        headers=headers,
        json={"name": "Зарядка", "habit_type": "checklist", "target_repeats": 1},
    )
    hid = (await c_resp.json())["data"]["habit_id"]

    # Check habit with widget_id in header
    check_resp = await web_client.post(f"/api/habits/{hid}/check", headers=headers)
    assert check_resp.status == 200
    res_data = await check_resp.json()
    assert res_data["ok"] is True
    assert res_data["synced"] is True

    # Verify bot.edit_message_text was called
    assert mock_bot.edit_message_text.called
    kwargs = mock_bot.edit_message_text.call_args.kwargs
    assert kwargs["chat_id"] == 1001
    assert kwargs["message_id"] == 2002
    assert "parse_mode" in kwargs

    # Verify TelegramBadRequest ("message is not modified") is caught cleanly
    mock_bot.edit_message_text.side_effect = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message",
    )

    # Next check call
    check_resp2 = await web_client.post(f"/api/habits/{hid}/check", headers=headers)
    assert check_resp2.status == 200
    res_data2 = await check_resp2.json()
    assert res_data2["ok"] is True
    assert res_data2["synced"] is True


async def test_send_widget_card_calling_conventions(mock_bot: MagicMock):
    """Verify send_widget_card works with both (bot, chat_id, ...) and (target_message, ...) callers."""
    from bot.services.widget_launcher import send_widget_card

    fake_msg = MagicMock()
    fake_msg.chat.id = 12345
    fake_msg.message_id = 9999
    mock_bot.send_message = AsyncMock(return_value=fake_msg)

    # Calling convention 1: keyword args with bot and chat_id (used by all routers)
    sent1 = await send_widget_card(
        bot=mock_bot,
        chat_id=12345,
        user_id=TEST_USER_ID,
        view="dashboard",
        local_date="2026-11-22",
        local_weekday=7,
        utc_offset=3,
        reply_markup=MagicMock(),
    )
    assert sent1.message_id == 9999
    assert mock_bot.send_message.called
    args1 = mock_bot.send_message.call_args.kwargs
    assert args1["chat_id"] == 12345
    assert "✨ Открыть панель дня" in str(args1["reply_markup"])

    # Calling convention 2: target_message
    mock_target = MagicMock()
    mock_target.bot = mock_bot
    mock_target.chat.id = 54321
    mock_target.from_user.id = TEST_USER_ID

    sent2 = await send_widget_card(
        target_message=mock_target,
        view="habits",
    )
    assert sent2.message_id == 9999
    args2 = mock_bot.send_message.call_args.kwargs
    assert args2["chat_id"] == 54321
