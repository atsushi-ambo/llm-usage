"""Configuration validation with helpful error messages."""

from __future__ import annotations

from pathlib import Path

from llm_usage.config import Settings


class ValidationError:
    """Represents a configuration validation error with helpful context."""

    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error", "warning", "info"

    def __str__(self) -> str:
        severity_symbol = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
            self.severity, "•"
        )
        return f"{severity_symbol} {self.field}: {self.message}"


def validate_settings(settings: Settings) -> list[ValidationError]:
    """Validate settings and return list of validation errors/warnings."""
    errors: list[ValidationError] = []

    if settings.budget_limit <= 0:
        errors.append(
            ValidationError(
                "LLM_USAGE_BUDGET_LIMIT",
                "Budget limit must be greater than 0",
                "error",
            )
        )

    if not (0 <= settings.budget_alert_threshold <= 1):
        errors.append(
            ValidationError(
                "LLM_USAGE_BUDGET_ALERT_THRESHOLD",
                "Alert threshold must be between 0 and 1",
                "error",
            )
        )

    if settings.days < 1:
        errors.append(
            ValidationError(
                "LLM_USAGE_DAYS",
                "Lookback period must be at least 1 day",
                "error",
            )
        )

    if settings.days > 365:
        errors.append(
            ValidationError(
                "LLM_USAGE_DAYS",
                "Lookback period should not exceed 365 days for performance",
                "warning",
            )
        )

    if settings.port < 1 or settings.port > 65535:
        errors.append(
            ValidationError(
                "LLM_USAGE_PORT",
                "Port must be between 1 and 65535",
                "error",
            )
        )

    if settings.dashboard_token_ttl < 60:
        errors.append(
            ValidationError(
                "LLM_USAGE_TOKEN_TTL",
                "Token TTL should be at least 60 seconds",
                "warning",
            )
        )

    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        errors.append(
            ValidationError(
                "LLM_USAGE_HOST",
                f"Host '{settings.host}' is not loopback. "
                "Non-local binds need --i-understand-no-auth and are less safe.",
                "warning",
            )
        )

    if settings.anthropic_admin_key and not settings.anthropic_admin_key.startswith(
        "sk-ant-admin"
    ):
        errors.append(
            ValidationError(
                "ANTHROPIC_ADMIN_KEY",
                "Anthropic admin key usually starts with 'sk-ant-admin'",
                "warning",
            )
        )

    if settings.openai_admin_key and not settings.openai_admin_key.startswith("sk-"):
        errors.append(
            ValidationError(
                "OPENAI_ADMIN_KEY",
                "OpenAI key should start with 'sk-'",
                "warning",
            )
        )

    if settings.xai_api_key and not settings.xai_api_key.startswith("xai-"):
        errors.append(
            ValidationError(
                "XAI_API_KEY",
                "xAI key should start with 'xai-'",
                "warning",
            )
        )

    for path, field in (
        (settings.claude_projects_dir, "CLAUDE_PROJECTS_DIR"),
        (settings.gemini_home_dir, "GEMINI_CLI_HOME"),
        (settings.codex_home_dir, "CODEX_HOME"),
        (settings.grok_home_dir, "GROK_HOME"),
    ):
        _validate_directory(path, field, errors)

    return errors


def _validate_directory(
    path: Path, field: str, errors: list[ValidationError]
) -> None:
    """Only flag paths that exist but are not directories (missing is OK)."""
    if path.exists() and not path.is_dir():
        errors.append(
            ValidationError(
                field,
                f"Path is not a directory: {path}",
                "error",
            )
        )


def format_validation_errors(errors: list[ValidationError]) -> str:
    """Format validation errors for display."""
    if not errors:
        return "✅ Configuration is valid"

    lines = ["Configuration validation results:"]
    by_severity: dict[str, list[ValidationError]] = {
        "error": [],
        "warning": [],
        "info": [],
    }
    for error in errors:
        by_severity.setdefault(error.severity, []).append(error)

    if by_severity["error"]:
        lines.append("\n❌ Errors (must fix):")
        for error in by_severity["error"]:
            lines.append(f"  • {error}")

    if by_severity["warning"]:
        lines.append("\n⚠️ Warnings (recommended to fix):")
        for error in by_severity["warning"]:
            lines.append(f"  • {error}")

    if by_severity["info"]:
        lines.append("\nℹ️ Info (for your reference):")
        for error in by_severity["info"]:
            lines.append(f"  • {error}")

    return "\n".join(lines)


def get_provider_setup_hint(provider: str) -> str:
    hints = {
        "claude": "Get admin key from Claude Console → Settings → Admin API keys",
        "openai": "Get org key from OpenAI Platform → Settings → Organization",
        "xai": "Get API key from x.ai console or management API",
        "cursor": "Get API key from Cursor Dashboard → API Keys or browser session token",
        "gemini": "Get API key from Google AI Studio → Get API Key",
        "openrouter": "Get API key from openrouter.ai/keys",
        "cohere": "Get API key from cohere.com/dashboard",
        "mistral": "Get API key from console.mistral.ai",
        "replicate": "Get API key from replicate.com/account/api-tokens",
        "huggingface": "Get API key from huggingface.co/settings/tokens",
    }
    return hints.get(provider, "Check provider documentation for API key setup")
