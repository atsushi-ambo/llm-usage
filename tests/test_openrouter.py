from datetime import date

import llm_usage.providers.openrouter as openrouter_module
from llm_usage.config import Settings
from llm_usage.models import SourceKind
from llm_usage.providers.openrouter import collect_openrouter


def _settings(api_key: str | None) -> Settings:
    return Settings(_env_file=None, OPENROUTER_API_KEY=api_key)


def test_no_key_returns_unavailable_with_setup_note():
    report = collect_openrouter(_settings(None), date(2026, 7, 1), date(2026, 7, 16))
    assert report.source == SourceKind.UNAVAILABLE
    assert not report.errors
    assert any("OPENROUTER_API_KEY" in n for n in report.notes)


def test_valid_key_with_limit_populates_cost_and_quota(monkeypatch):
    monkeypatch.setattr(
        openrouter_module,
        "_fetch_key_info",
        lambda key: {"usage": 12.5, "limit": 50.0, "is_free_tier": False},
    )
    report = collect_openrouter(_settings("sk-or-fake"), date(2026, 7, 1), date(2026, 7, 16))
    assert report.source == SourceKind.API
    assert report.cost_usd == 12.5
    assert report.meta["quota"]["used_percent"] == 25.0
    assert report.meta["quota"]["plan"] == "Pay-as-you-go"


def test_free_tier_with_no_limit_has_no_quota_block(monkeypatch):
    monkeypatch.setattr(
        openrouter_module,
        "_fetch_key_info",
        lambda key: {"usage": 0.0, "limit": None, "is_free_tier": True},
    )
    report = collect_openrouter(_settings("sk-or-fake"), date(2026, 7, 1), date(2026, 7, 16))
    assert report.source == SourceKind.API
    assert "quota" not in report.meta
    assert any("free tier" in n.lower() for n in report.notes)


def test_api_error_is_recorded_and_source_stays_unavailable(monkeypatch):
    def _raise(key):
        raise RuntimeError("HTTP 401 for https://openrouter.ai/api/v1/auth/key")

    monkeypatch.setattr(openrouter_module, "_fetch_key_info", _raise)
    report = collect_openrouter(_settings("bad-key"), date(2026, 7, 1), date(2026, 7, 16))
    assert report.source == SourceKind.UNAVAILABLE
    assert any("401" in e for e in report.errors)
