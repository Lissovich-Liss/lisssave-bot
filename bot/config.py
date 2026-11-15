"""Configuration management via Pydantic Settings."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_AD_CAPTION = (
    "⚡️ Загружено через бота\n\n"
    "<blockquote>🔒 Стабильный доступ без ограничений — @lissvpnbot</blockquote>"
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(..., alias="BOT_TOKEN", description="Telegram Bot token from @BotFather")
    telegram_api_id: int = Field(default=0, alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    local_api_url: str = Field(
        default="http://telegram-bot-api:8081",
        alias="LOCAL_API_URL",
        description="Base URL of local telegram-bot-api server",
    )

    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        alias="ADMIN_IDS",
        description="Comma-separated Telegram user IDs of administrators",
    )

    proxy_url: str = Field(
        default="",
        alias="PROXY_URL",
        description="Optional HTTP/SOCKS5 proxy URL for yt-dlp",
    )

    ad_caption: str = Field(
        default=DEFAULT_AD_CAPTION,
        alias="AD_CAPTION",
        description="Native promotional caption appended under downloaded media",
    )

    max_concurrent_downloads: int = Field(
        default=2,
        alias="MAX_CONCURRENT_DOWNLOADS",
        ge=1,
        le=10,
    )
    max_file_size_mb: int = Field(
        default=1950,
        alias="MAX_FILE_SIZE_MB",
        ge=50,
        le=2000,
    )

    data_dir: Path = Field(default=BASE_DIR / "data", alias="DATA_DIR")
    db_path: Path = Field(default=BASE_DIR / "data" / "database.sqlite3", alias="DB_PATH")
    cookies_file: Path = Field(default=BASE_DIR / "data" / "cookies.txt", alias="COOKIES_FILE")
    temp_downloads_dir: Path = Field(
        default=BASE_DIR / "data" / "temp_downloads",
        alias="TEMP_DOWNLOADS_DIR",
    )
    welcome_banner_path: Path = Field(
        default=BASE_DIR / "assets" / "welcome_banner.png",
        alias="WELCOME_BANNER_PATH",
    )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: Any) -> list[int]:
        """Parse ADMIN_IDS from comma-separated string, int, or list."""
        if value is None or value == "":
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            cleaned = value.strip().strip("[]")
            if not cleaned:
                return []
            result: list[int] = []
            for item in cleaned.split(","):
                part = item.strip()
                if part:
                    result.append(int(part))
            return result
        if isinstance(value, (list, tuple, set)):
            return [int(x) for x in value]
        return []

    @field_validator("ad_caption", mode="before")
    @classmethod
    def unescape_newlines(cls, value: Any) -> str:
        """Allow literal \\n sequences in .env AD_CAPTION."""
        if isinstance(value, str) and value.strip():
            return value.replace("\\n", "\n")
        return DEFAULT_AD_CAPTION

    @property
    def max_file_size_bytes(self) -> int:
        """Return maximum allowed media size in bytes."""
        return self.max_file_size_mb * 1024 * 1024

    @property
    def valid_cookies_path(self) -> str | None:
        """Return path to cookies.txt if it exists and is non-empty."""
        if self.cookies_file.exists() and self.cookies_file.is_file():
            if self.cookies_file.stat().st_size > 0:
                return str(self.cookies_file.resolve())
        return None


settings = Settings()
