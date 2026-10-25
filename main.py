"""Application entry point for Habit Tracker Telegram Bot."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.database.db import init_db
from bot.handlers import checkin, habits, start, stats, timer
from bot.middlewares.user_check import UserCheckMiddleware
from bot.services.scheduler import init_scheduler


def setup_logging() -> None:
    """Configure structured logging for the application."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main() -> None:
    """Initialize resources, schedule tasks, and start polling."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting Habit Tracker Bot...")

    # Verify BOT_TOKEN
    if not settings.BOT_TOKEN or "YOUR_TELEGRAM_BOT_TOKEN" in settings.BOT_TOKEN or "FakeToken" in settings.BOT_TOKEN:
        logger.warning(
            "⚠️ BOT_TOKEN is not configured or contains placeholder text! "
            "Please update BOT_TOKEN in .env to connect to Telegram."
        )

    # 1. Initialize SQLite database and tables
    await init_db(settings.DB_PATH)

    # 2. Instantiate Bot and Dispatcher
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # 3. Register Middlewares
    user_middleware = UserCheckMiddleware()
    dp.message.outer_middleware(user_middleware)
    dp.callback_query.outer_middleware(user_middleware)

    # 4. Include Routers
    dp.include_router(start.router)
    dp.include_router(habits.router)
    dp.include_router(timer.router)
    dp.include_router(checkin.router)
    dp.include_router(stats.router)

    # 5. Initialize & Start APScheduler
    scheduler = init_scheduler(bot)
    scheduler.start()
    logger.info("APScheduler initialized and running.")

    # 6. Start Polling with clean shutdown
    try:
        logger.info("Bot started polling.")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down bot and scheduler...")
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot process stopped.")
