"""HMAC-SHA256 validation for Telegram Mini App initData and aiohttp authentication middleware."""

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from aiohttp import web

from bot.config import settings
from bot.database import users_repo

logger = logging.getLogger(__name__)


class InitDataAuthError(Exception):
    """Raised when Telegram WebApp initData signature validation fails."""
    pass


def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 86400,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Validate Telegram WebApp initData string using HMAC-SHA256 according to Bot API specifications.
    
    Returns parsed dictionary containing user info, auth_date, and other query params.
    Raises InitDataAuthError on any verification failure.
    """
    if not init_data or not isinstance(init_data, str):
        raise InitDataAuthError("Missing or empty initData")

    parsed_pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    if not parsed_pairs:
        raise InitDataAuthError("Malformed initData query string")

    params: Dict[str, str] = dict(parsed_pairs)
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise InitDataAuthError("Missing hash signature in initData")

    # Sort remaining parameters alphabetically by key
    sorted_pairs = sorted(params.items(), key=lambda kv: kv[0])
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_pairs)

    # Calculate HMAC-SHA256 signature
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataAuthError("Invalid HMAC signature")

    # Verify auth_date
    auth_date_str = params.get("auth_date")
    if not auth_date_str or not auth_date_str.isdigit():
        raise InitDataAuthError("Missing or invalid auth_date")

    auth_date = int(auth_date_str)
    current_time = now_ts if now_ts is not None else int(time.time())

    if current_time - auth_date > max_age_seconds:
        raise InitDataAuthError("Authentication session expired (auth_date too old)")

    if auth_date > current_time + 300:
        raise InitDataAuthError("Authentication timestamp is in the future")

    # Parse user object
    user_json = params.get("user")
    if not user_json:
        raise InitDataAuthError("Missing user payload in initData")

    try:
        user_data = json.loads(user_json)
        if not isinstance(user_data, dict) or "id" not in user_data or not isinstance(user_data["id"], int):
            raise InitDataAuthError("Invalid user data structure")
    except (json.JSONDecodeError, TypeError) as e:
        raise InitDataAuthError(f"Failed to decode user JSON: {e}") from e

    return {
        "user": user_data,
        "auth_date": auth_date,
        "query_id": params.get("query_id"),
        "raw_params": params,
    }


@web.middleware
async def telegram_auth_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """
    Middleware requiring valid Telegram WebApp initData on all /api/* routes.
    Injects authenticated user record and local timezone metadata into request["auth"].
    """
    # Only protect API routes
    if not request.path.startswith("/api/"):
        return await handler(request)

    init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("init_data")
    if not init_data:
        return web.json_response(
            {"ok": False, "error": "Missing X-Telegram-Init-Data authorization header"},
            status=401,
        )

    bot_token = request.app.get("bot_token") or settings.BOT_TOKEN
    max_age = request.app.get("auth_max_age") or settings.WEBAPP_AUTH_MAX_AGE

    try:
        validated = validate_telegram_init_data(
            init_data=init_data,
            bot_token=bot_token,
            max_age_seconds=max_age,
        )
    except InitDataAuthError as e:
        logger.warning("Telegram WebApp auth failure on %s: %s", request.path, e)
        return web.json_response({"ok": False, "error": str(e)}, status=401)
    except Exception as e:
        logger.error("Unexpected error validating initData: %s", e)
        return web.json_response({"ok": False, "error": "Authentication processing error"}, status=401)

    tg_user = validated["user"]
    user_id = tg_user["id"]
    username = tg_user.get("username")

    # Fetch user from DB or register with defaults
    user = await users_repo.get_user(user_id)
    if not user:
        user = await users_repo.create_or_update_user(
            user_id=user_id,
            username=username,
            utc_offset=3,
            checkin_time="21:00",
        )

    utc_offset = user.get("utc_offset", 3)
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc + timedelta(hours=utc_offset)
    local_date = local_now.strftime("%Y-%m-%d")
    local_weekday = local_now.isoweekday()

    wid = request.headers.get("X-Widget-Id") or request.query.get("wid")

    request["auth"] = {
        "user_id": user_id,
        "user": user,
        "tg_user": tg_user,
        "utc_offset": utc_offset,
        "local_now": local_now,
        "local_date": local_date,
        "local_weekday": local_weekday,
        "server_ts": int(now_utc.timestamp()),
    }
    request["wid"] = wid

    return await handler(request)
