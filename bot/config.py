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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
