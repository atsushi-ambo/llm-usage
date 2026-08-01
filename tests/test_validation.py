"""Tests for configuration validation."""

from llm_usage.config import Settings
from llm_usage.validation import ValidationError, validate_settings, format_validation_errors


def test_validate_settings_valid():
    """Test validation with valid settings."""
    settings = Settings(
        budget_limit=100.0,
        budget_alert_threshold=0.9,
        days=30,
        port=8765,
        host="127.0.0.1",
    )
    errors = validate_settings(settings)
    assert len(errors) == 0


def test_validate_settings_invalid_budget():
    """Test validation with invalid budget settings."""
    settings = Settings(
        budget_limit=-10.0,
        budget_alert_threshold=1.5,
    )
    errors = validate_settings(settings)
    assert len(errors) > 0
    assert any("Budget limit must be greater than 0" in e.message for e in errors)
    assert any("Alert threshold must be between 0 and 1" in e.message for e in errors)


def test_validate_settings_invalid_port():
    """Test validation with invalid port."""
    settings = Settings(port=99999)
    errors = validate_settings(settings)
    assert len(errors) > 0
    assert any("Port must be between 1 and 65535" in e.message for e in errors)


def test_validate_settings_unusual_host():
    """Test validation with unusual host (warning)."""
    settings = Settings(host="192.168.1.1")
    errors = validate_settings(settings)
    assert len(errors) > 0
    assert any("not loopback" in e.message or "Unusual host" in e.message for e in errors)


def test_format_validation_errors():
    """Test formatting of validation errors."""
    errors = [
        ValidationError("TEST", "Test error", "error"),
        ValidationError("WARN", "Test warning", "warning"),
    ]
    output = format_validation_errors(errors)
    assert "❌ TEST: Test error" in output
    assert "⚠️ WARN: Test warning" in output


def test_format_validation_errors_empty():
    """Test formatting with no errors."""
    output = format_validation_errors([])
    assert "✅ Configuration is valid" in output
