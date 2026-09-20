"""Typed settings, read once at startup from the environment / ``.env``.

Secrets never leave this object: ``SecretStr`` keeps them out of reprs, logs
and tracebacks. The guardrail constants are deliberately *not* here — they live
in ``app.engine.guards`` and change only with a commit.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_PREFIX = "/api/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Optional until milestone 7: without it every Gemini job is *disabled* (loudly:
    # /health reports it, startup logs it, the client raises instead of calling).
    gemini_api_key: SecretStr | None = None
    gemini_model: str = Field(default="gemini-2.5-flash", min_length=1)
    api_bearer_token: SecretStr

    data_dir: Path = Path("/data")
    db_path: Path = Path("/data/health.db")
    photo_dir: Path = Path("/data/photos")
    backup_dir: Path = Path("/data/backups")
    tz: str = "Europe/Madrid"
    log_level: str = "INFO"
    scheduler_enabled: bool = True  # tests turn the in-process jobs off

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        # An empty GEMINI_API_KEY= line means "not configured", never "call unauthenticated".
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def gemini_enabled(self) -> bool:
        return self.gemini_api_key is not None

    @field_validator("api_bearer_token")
    @classmethod
    def _token_strong_enough(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value().strip()) < 32:
            raise ValueError("API_BEARER_TOKEN must be at least 32 characters (openssl rand -hex 32)")
        return v

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
