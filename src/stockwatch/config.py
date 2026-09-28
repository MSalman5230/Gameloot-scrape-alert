"""Process configuration, read from the environment (and `.env` when present)."""

from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "memory://" runs with an in-process store (dev/demo; nothing is persisted).
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_default_db: str = "gamelootScrape"

    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""  # comma-separated

    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # Initial value only; the live value is stored in the DB and edited from the dashboard.
    default_max_concurrent_runs: int = Field(5, ge=1, le=50)
    # The scheduler sleeps until the next category is due and is woken by dashboard edits; this caps
    # the sleep, i.e. how late it notices changes made directly in the database.
    scheduler_tick_seconds: float = 60.0
    run_timeout_seconds: float = 15 * 60

    @cached_property
    def chat_ids(self) -> list[str]:
        return [c.strip() for c in self.telegram_chat_ids.split(",") if c.strip()]
