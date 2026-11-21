"""Embedded aiohttp web server hosting the interactive Telegram Mini App widget and API."""

import logging
import os
from pathlib import Path
from typing import Optional

from aiogram import Bot
from aiohttp import web

from bot.config import settings
from bot.web import api_dashboard, api_habits, api_health
from bot.web.auth import telegram_auth_middleware

logger = logging.getLogger(__name__)

# Base path for frontend static assets
WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


@web.middleware
async def error_handling_middleware(request: web.Request, handler) -> web.Response:
    """Catch unhandled exceptions and return JSON error for API requests."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled server error processing %s: %s", request.path, e)
        if request.path.startswith("/api/"):
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        raise


async def health_check_handler(request: web.Request) -> web.Response:
    """Public health check endpoint."""
    return web.json_response({"status": "ok"})


async def index_html_handler(request: web.Request) -> web.Response:
    """Serve the single-page widget HTML."""
    index_file = WEB_DIR / "index.html"
    if not index_file.is_file():
        return web.Response(text="Widget interface not found", status=404)

    content = index_file.read_text(encoding="utf-8")
    return web.Response(
        text=content,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def static_assets_handler(request: web.Request) -> web.Response:
    """Serve static frontend assets with path traversal protection."""
    filename = request.match_info.get("filename", "")

    # Strict filename whitelist and path traversal prevention
    allowed_files = {
        "styles.css": ("text/css", "utf-8"),
        "app.js": ("application/javascript", "utf-8"),
    }

    if filename not in allowed_files or "/" in filename or "\\" in filename or ".." in filename:
        return web.Response(text="Forbidden or not found", status=403)

    target_file = (WEB_DIR / filename).resolve()
    # Extra check: must be inside WEB_DIR
    try:
        target_file.relative_to(WEB_DIR.resolve())
    except ValueError:
        return web.Response(text="Forbidden", status=403)

    if not target_file.is_file():
        return web.Response(text="Asset not found", status=404)

    content_type, charset = allowed_files[filename]
    content = target_file.read_text(encoding="utf-8")
    return web.Response(
        text=content,
        content_type=content_type,
        charset=charset,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def create_web_app(bot: Bot, bot_token: Optional[str] = None) -> web.Application:
    """Factory creating and configuring the aiohttp Web Application."""
    app = web.Application(
        middlewares=[
            error_handling_middleware,
            telegram_auth_middleware,
        ]
    )

    app["bot"] = bot
    app["bot_token"] = bot_token or settings.BOT_TOKEN

    # Basic routes
    app.router.add_get("/health", health_check_handler)
    app.router.add_get("/", index_html_handler)
    app.router.add_get("/widget", index_html_handler)
    app.router.add_get("/widget/assets/{filename}", static_assets_handler)

    # API routes
    app.add_routes(api_dashboard.routes)
    app.add_routes(api_habits.routes)
    app.add_routes(api_health.routes)

    return app


async def start_web_server(bot: Bot, host: str, port: int) -> web.AppRunner:
    """Start embedded aiohttp server on the current asyncio event loop."""
    app = create_web_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Embedded web server started at http://%s:%d", host, port)
    return runner


async def stop_web_server(runner: web.AppRunner) -> None:
    """Cleanly stop and cleanup the embedded web server."""
    logger.info("Stopping embedded web server...")
    await runner.cleanup()
    logger.info("Embedded web server stopped.")
