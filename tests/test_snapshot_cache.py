import threading
import time
from datetime import date

import llm_usage.providers as providers_module
from llm_usage.config import Settings
from llm_usage.models import AggregateReport
from llm_usage.providers import collect_all_cached


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_second_call_within_ttl_reuses_snapshot(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None, *, quota_only=False):
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

    def fake_collect_all(settings, days=None, *, quota_only=False):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=7, ttl_s=60.0)
    collect_all_cached(_settings(), days=7, ttl_s=60.0, force_refresh=True)

    assert len(calls) == 2


def test_zero_ttl_never_caches(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None, *, quota_only=False):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=7, ttl_s=0)
    collect_all_cached(_settings(), days=7, ttl_s=0)

    assert len(calls) == 2


def test_different_days_windows_do_not_share_a_snapshot(monkeypatch):
    calls = []

    def fake_collect_all(settings, days=None, *, quota_only=False):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    collect_all_cached(_settings(), days=7, ttl_s=60.0)
    collect_all_cached(_settings(), days=30, ttl_s=60.0)

    assert calls == [7, 30]


def test_different_profiles_do_not_share_a_snapshot(monkeypatch):
    calls: list[int | None] = []

    def fake_collect_all(settings, days=None, *, quota_only=False):
        calls.append(days)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)
    monkeypatch.setattr("llm_usage.config.get_active_profile", lambda: "work")
    collect_all_cached(_settings(), days=7, ttl_s=60.0)
    monkeypatch.setattr("llm_usage.config.get_active_profile", lambda: "home")
    collect_all_cached(_settings(), days=7, ttl_s=60.0)
    assert len(calls) == 2


def test_overlapping_calls_share_one_collection(monkeypatch):
    calls: list[int | None] = []
    started = threading.Event()
    release = threading.Event()

    def fake_collect_all(settings, days=None, *, quota_only=False):
        calls.append(days)
        started.set()
        assert release.wait(timeout=2)
        return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 10))

    monkeypatch.setattr(providers_module, "collect_all", fake_collect_all)

    results: list[AggregateReport] = []

    def worker() -> None:
        results.append(collect_all_cached(_settings(), days=7, ttl_s=60.0))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    assert started.wait(timeout=2)
    t2.start()
    time.sleep(0.05)
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert len(calls) == 1
    assert len(results) == 2
