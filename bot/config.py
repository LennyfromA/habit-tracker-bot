"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Configuration settings for the Habit Tracker Bot."""

    BOT_TOKEN: str = Field(
        default="YOUR_TELEGRAM_BOT_TOKEN_HERE",
        description="Telegram bot token obtained from @BotFather",
    )
    DB_PATH: str = Field(
        default="habit_tracker.db",
        description="Path to the SQLite database file",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level for standard output",
    )

    # Telegram Mini App / HTML Widget configuration
    WIDGET_BASE_URL: str = Field(
        default="",
        description="Public HTTPS base URL for the widget, e.g. https://bot.example.com",
    )
    WEB_HOST: str = Field(
        default="0.0.0.0",
        description="Bind host for embedded aiohttp server",
    )
    WEB_PORT: int = Field(
        default=8080,
        description="Bind port for embedded aiohttp server",
    )
    WEBAPP_AUTH_MAX_AGE: int = Field(
        default=86400,
        description="Max initData auth_date age in seconds",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
