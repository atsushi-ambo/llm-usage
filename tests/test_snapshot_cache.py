from datetime import date

import llm_usage.providers as providers_module
from llm_usage.config import Settings
from llm_usage.models import AggregateReport
from llm_usage.providers import collect_all_cached


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_second_call_within_ttl_reuses_snapshot(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    r1 = collect_all_cached(_settings(), days=7, ttl_s=60.0)
    r2 = collect_all_cached(_settings(), days=7, ttl_s=60.0)

    assert isinstance(r1, AggregateReport)
    assert isinstance(r2, AggregateReport)
    assert len(calls) == 1


def test_force_refresh_bypasses_cache(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=7, ttl_s=60.0)
    collect_all_cached(_settings(), days=7, ttl_s=60.0, force_refresh=True)

    assert len(calls) == 2


def test_zero_ttl_never_caches(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=7, ttl_s=0)
    collect_all_cached(_settings(), days=7, ttl_s=0)

    assert len(calls) == 2


def test_different_days_windows_do_not_share_a_snapshot(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=7, ttl_s=60.0)
    collect_all_cached(_settings(), days=30, ttl_s=60.0)

    assert calls == [7, 30]
