"""Configuration loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_config_dir() -> Path:
    return Path.home() / ".config" / "llm-usage"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", Path.home() / ".config" / "llm-usage" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Anthropic
    anthropic_admin_key: str | None = Field(default=None, alias="ANTHROPIC_ADMIN_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # OpenAI
    openai_admin_key: str | None = Field(default=None, alias="OPENAI_ADMIN_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    # xAI
    xai_api_key: str | None = Field(default=None, alias="XAI_API_KEY")
    xai_management_key: str | None = Field(default=None, alias="XAI_MANAGEMENT_KEY")
    xai_team_id: str | None = Field(default=None, alias="XAI_TEAM_ID")

    # Cursor
    cursor_api_key: str | None = Field(default=None, alias="CURSOR_API_KEY")
    cursor_session_token: str | None = Field(default=None, alias="CURSOR_SESSION_TOKEN")

    # Gemini
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")

    # App
    days: int = Field(default=30, alias="LLM_USAGE_DAYS")
    port: int = Field(default=8765, alias="LLM_USAGE_PORT")
    host: str = Field(default="127.0.0.1", alias="LLM_USAGE_HOST")

    # Local log roots (override if needed)
    claude_projects_dir: Path = Field(
        default_factory=lambda: Path.home() / ".claude" / "projects",
        alias="CLAUDE_PROJECTS_DIR",
    )
    gemini_home_dir: Path = Field(
        default_factory=lambda: Path.home() / ".gemini",
        alias="GEMINI_CLI_HOME",
    )
    claude_credentials_path: Path = Field(
        default_factory=lambda: Path.home() / ".claude" / ".credentials.json",
        alias="CLAUDE_CREDENTIALS_PATH",
    )


def load_settings() -> Settings:
    return Settings()
