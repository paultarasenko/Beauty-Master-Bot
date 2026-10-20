from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Вся конфигурация приложения читается из .env / переменных окружения."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/beauty_bot"
    admin_telegram_id: int = 0
    timezone: str = "Europe/Minsk"
    slot_interval_minutes: int = 30
    reminder_hours_before: int = 24
    reminder_poll_interval_seconds: int = 60


settings = Settings()