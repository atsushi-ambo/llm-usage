"""Menubar quota_only path must not walk local log trees."""

from datetime import date

import llm_usage.providers as providers_module
import llm_usage.providers.claude as claude_mod
import llm_usage.providers.codex as codex_mod
import llm_usage.providers.xai as xai_mod
import llm_usage.quota as quota_mod
from llm_usage.config import Settings
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind
from llm_usage.providers import collect_all, collect_all_cached


def _settings() -> Settings:
    return Settings(_env_file=None)


def _empty(pid: ProviderId, start: date, end: date) -> ProviderReport:
    return ProviderReport(
        provider=pid,
        display_name=pid.value,
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
    )


def test_quota_only_skips_claude_log_scan(monkeypatch):
    called = {"scan": 0}

    def boom(*_a, **_k):
        called["scan"] += 1
        raise AssertionError("log scan should not run in quota_only")

    monkeypatch.setattr(claude_mod, "_scan_local_logs", boom)
    monkeypatch.setattr(
        claude_mod,
        "_read_claude_cred_meta",
        lambda _p: {"plan": "pro", "access_token": None},
    )
    start, end = date(2026, 7, 1), date(2026, 7, 2)
    report = claude_mod.collect_claude(_settings(), start, end, quota_only=True)
    assert called["scan"] == 0
    assert report.provider == ProviderId.CLAUDE
    assert report.models == []


def test_quota_only_skips_codex_session_scan(monkeypatch):
    monkeypatch.setattr(
        codex_mod,
        "_scan_sessions",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no session scan")),
    )
    monkeypatch.setattr(codex_mod, "_read_codex_auth", lambda _p: None)
    start, end = date(2026, 7, 1), date(2026, 7, 2)
    report = codex_mod.collect_codex(_settings(), start, end, quota_only=True)
    assert report.models == []
    assert report.daily == []


def test_quota_only_skips_grok_log_scan(monkeypatch):
    monkeypatch.setattr(
        xai_mod,
        "_scan_grok_build",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no grok log scan")),
    )
    monkeypatch.setattr(
        xai_mod,
        "_fetch_live_credits",
        lambda _home: {
            "subscription_tier": "X Premium",
            "credit_usage_percent": 12.0,
            "period": {"start": "2026-07-15", "end": "2026-07-22", "type": "weekly"},
            "product_usage": [],
        },
    )
    start, end = date(2026, 7, 1), date(2026, 7, 2)
    report = xai_mod.collect_xai(_settings(), start, end, quota_only=True)
    assert report.meta.get("quota", {}).get("used_percent") == 12.0
    assert report.models == []
    assert report.daily == []


def test_collect_all_quota_only_skips_openai_platform(monkeypatch):
    """Platform org usage API is not needed for menubar bars."""
    calls: list[str] = []

    def track_openai(settings, start, end, **_kwargs):
        calls.append("openai")
        return _empty(ProviderId.OPENAI, start, end)

    monkeypatch.setattr(providers_module, "collect_openai", track_openai)
    monkeypatch.setattr(
        providers_module,
        "collect_claude",
        lambda s, a, b, quota_only=False: _empty(ProviderId.CLAUDE, a, b),
    )
    monkeypatch.setattr(
        providers_module,
        "collect_codex",
        lambda s, a, b, quota_only=False: _empty(ProviderId.CODEX, a, b),
    )
    monkeypatch.setattr(
        providers_module,
        "collect_xai",
        lambda s, a, b, quota_only=False: _empty(ProviderId.GROK, a, b),
    )
    monkeypatch.setattr(
        providers_module,
        "collect_cursor",
        lambda s, a, b, quota_only=False: _empty(ProviderId.CURSOR, a, b),
    )
    monkeypatch.setattr(
        providers_module,
        "collect_gemini",
        lambda s, a, b, quota_only=False: _empty(ProviderId.GEMINI, a, b),
    )
    monkeypatch.setattr(
        providers_module,
        "collect_openrouter",
        lambda s, a, b, quota_only=False: _empty(ProviderId.OPENROUTER, a, b),
    )

    collect_all(_settings(), days=1, quota_only=True)
    assert calls == []


def test_collect_all_cached_quota_uses_separate_cache_key(monkeypatch, tmp_path):
    monkeypatch.setattr(quota_mod, "cache_dir", lambda: tmp_path)
    calls: list[bool] = []

    def fake_collect_all(settings, days=None, *, quota_only=False):
        calls.append(quota_only)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 2))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=1, ttl_s=60, quota_only=True)
    collect_all_cached(_settings(), days=1, ttl_s=60, quota_only=False)
    # two different cache keys → two collects
    assert calls == [True, False]
    assert (tmp_path / "report_snapshot_quota_1.json").exists()
    assert (tmp_path / "report_snapshot_1.json").exists()
