"""Runtime configuration. Every knob is an env var so prod, CI and local differ only in .env."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_env: str = "dev"
    secret_key: str = "dev-only-change-me"
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'opspilot.db'}"
    app_timezone: str = "Asia/Kolkata"
    cookie_secure: bool = False
    session_hours: int = 12

    google_api_key: str = ""
    llm_fake: bool = False  # offline dev/tests: deterministic scripted model, labelled fake-llm
    llm_primary_model: str = "gemini-2.5-flash"
    llm_fallback_model: str = "gemini-2.5-flash-lite"
    llm_timeout_s: float = 40.0
    llm_max_runs_per_min: int = 8

    conf_high: float = 0.75
    conf_block: float = 0.5
    sla_sweep_seconds: int = 30
    run_background_workers: bool = True

    mail_provider: str = "mock"  # mock | smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "opspilot@example.com"

    intake_webhook_secret: str = ""
    demo_mode: bool = True

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.google_api_key) or self.llm_fake


@lru_cache
def get_settings() -> Settings:
    return Settings()
