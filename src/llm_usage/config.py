"""Configuration loaded from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_config_dir() -> Path:
    return Path.home() / ".config" / "llm-usage"


_GLOBAL_ENV_FILE = Path.home() / ".config" / "llm-usage" / ".env"
_ACTIVE_PROFILE_FILE = Path.home() / ".config" / "llm-usage" / "active_profile"


def get_profile_env_file(profile: str | None = None) -> Path:
    """Get the env file path for a specific profile (or the default .env)."""
    if profile:
        # Prevent path traversal via profile name.
        safe = "".join(c for c in profile if c.isalnum() or c in ("-", "_", "."))
        if not safe or safe != profile:
            raise ValueError(
                "Profile name must be alphanumeric with optional - _ ."
            )
        return _default_config_dir() / f".env.{safe}"
    return _GLOBAL_ENV_FILE


def list_profiles() -> list[str]:
    """List available configuration profiles (from .env.<name> files)."""
    config_dir = _default_config_dir()
    if not config_dir.exists():
        return []
    profiles: list[str] = []
    for env_file in config_dir.glob(".env.*"):
        if env_file.is_file():
            profiles.append(env_file.name.removeprefix(".env."))
    return sorted(profiles)


def get_active_profile() -> str | None:
    """Return active profile name from env or active_profile file."""
    env_profile = os.environ.get("LLM_USAGE_PROFILE", "").strip()
    if env_profile:
        return env_profile
    try:
        if _ACTIVE_PROFILE_FILE.is_file():
            name = _ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip()
            return name or None
    except OSError:
        pass
    return None


def set_active_profile(profile: str | None) -> None:
    """Persist active profile (None clears to default .env)."""
    config_dir = _default_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    if not profile:
        try:
            _ACTIVE_PROFILE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return
    # Validate by resolving path.
    get_profile_env_file(profile)
    _ACTIVE_PROFILE_FILE.write_text(profile + "\n", encoding="utf-8")
    try:
        os.chmod(_ACTIVE_PROFILE_FILE, 0o600)
    except OSError:
        pass


class Settings(BaseSettings):
    # Deliberately does NOT include a bare ".env" (current-working-directory)
    # path here: llm-usage often gets run from inside other projects' repos,
    # and auto-loading whatever ".env" happens to sit in the CWD would let an
    # untrusted checkout override API keys, rebind the dashboard host, or
    # redirect log-scan directories. Opt in per-invocation with
    # LLM_USAGE_ENV_FILE=path/to/.env (a real env var, not something a repo
    # can set just by existing on disk).
    model_config = SettingsConfigDict(
        env_file=_GLOBAL_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow both field names (budget_limit) and env aliases (LLM_USAGE_BUDGET_LIMIT)
        # so tests and programmatic Settings(...) construction work.
        populate_by_name=True,
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

    # OpenRouter
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    # Optional extra providers (usage APIs mostly unavailable — config-only)
    cohere_api_key: str | None = Field(default=None, alias="COHERE_API_KEY")
    mistral_api_key: str | None = Field(default=None, alias="MISTRAL_API_KEY")
    replicate_api_key: str | None = Field(default=None, alias="REPLICATE_API_KEY")
    huggingface_api_key: str | None = Field(default=None, alias="HUGGINGFACE_API_KEY")

    # Budget
    budget_limit: float = Field(default=100.0, alias="LLM_USAGE_BUDGET_LIMIT")
    budget_alert_threshold: float = Field(
        default=0.9, alias="LLM_USAGE_BUDGET_ALERT_THRESHOLD"
    )

    # Dashboard auth cookie / HTTPS hints
    dashboard_token_ttl: int = Field(default=3600, alias="LLM_USAGE_TOKEN_TTL")
    require_https: bool = Field(default=False, alias="LLM_USAGE_REQUIRE_HTTPS")

    # Debug
    debug_mode: bool = Field(default=False, alias="LLM_USAGE_DEBUG")
    verbose_logging: bool = Field(default=False, alias="LLM_USAGE_VERBOSE")

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
    codex_home_dir: Path = Field(
        default_factory=lambda: Path.home() / ".codex",
        alias="CODEX_HOME",
    )
    grok_home_dir: Path = Field(
        default_factory=lambda: Path.home() / ".grok",
        alias="GROK_HOME",
    )


def load_settings(profile: str | None = None) -> Settings:
    """Load settings from the default or named profile env file."""
    resolved_profile = profile if profile is not None else get_active_profile()
    base_env_file = (
        get_profile_env_file(resolved_profile)
        if resolved_profile
        else _GLOBAL_ENV_FILE
    )
    extra_env_file = os.environ.get("LLM_USAGE_ENV_FILE")
    if extra_env_file:
        return Settings(_env_file=(base_env_file, extra_env_file))  # type: ignore[call-arg]
    # Always pass the resolved base so profile switch is honored even when
    # the default model_config env_file points at the global .env.
    return Settings(_env_file=base_env_file)  # type: ignore[call-arg]
