from datetime import date

from llm_usage.menubar_core import active_report
from llm_usage.models import AggregateReport, ProviderReport, ProviderId, SourceKind


def report(pct=None, requests=0):
    return AggregateReport(
        period_start=date.today(),
        period_end=date.today(),
        providers=[
            ProviderReport(
                provider=ProviderId.CODEX,
                display_name="Codex",
                source=SourceKind.SUBSCRIPTION,
                requests=requests,
                meta={"quota": {"used_percent": pct}},
            ),
            ProviderReport(
                provider=ProviderId.CURSOR, display_name="Cursor", source=SourceKind.UNAVAILABLE
            ),
        ],
    )


def test_idle_expires_and_new_usage_returns():
    activity = {}
    assert not active_report(report(22), activity, now=9900).providers
    assert len(active_report(report(23), activity, now=10000).providers) == 1
    assert len(active_report(report(23), activity, now=11000).providers) == 1
    assert not active_report(report(23), activity, now=11800).providers
    assert len(active_report(report(24), activity, now=12000).providers) == 1


def test_unused_and_quota_reset_do_not_count_as_activity():
    activity = {}
    assert not active_report(report(0), activity, now=10000).providers
    active_report(report(100), activity, now=11000)
    assert not active_report(report(0), activity, now=13000).providers
    assert len(active_report(report(1), activity, now=14000).providers) == 1


def test_missing_quota_does_not_make_recovery_new_usage():
    activity = {}
    active_report(report(23), activity, now=10000)
    active_report(report(), activity, now=12000)
    assert not active_report(report(23), activity, now=13000).providers


def test_persistence_and_nonquota_activity():
    import json

    activity = {}
    assert not active_report(report(requests=2), activity, now=9900).providers
    assert len(active_report(report(requests=3), activity, now=10000).providers) == 1
    restored = json.loads(json.dumps(activity))
    assert not active_report(report(requests=3), restored, now=12000).providers
    assert len(active_report(report(requests=4), restored, now=13000).providers) == 1


def test_old_positive_balance_never_proves_recent_activity():
    activity = {}
    assert not active_report(report(57), activity, now=10000).providers
    assert not active_report(report(57), activity, now=10100).providers


def test_migrate_false_initial_activity_without_losing_baseline():
    activity = {"codex": {"readings": {"quota:primary": 57}, "last_active": 9999}}
    assert not active_report(report(57), activity, now=10000).providers
    assert len(active_report(report(58), activity, now=10100).providers) == 1


def test_stale_cache_increase_is_not_activity():
    activity = {}
    active_report(report(23), activity, now=10000)
    stale = report(57)
    stale.providers[0].meta["quota_stale"] = True
    assert not active_report(stale, activity, now=10100).providers
    assert not active_report(report(23), activity, now=10200).providers


def test_newly_available_window_only_establishes_baseline():
    activity = {}
    active_report(report(23), activity, now=10000)
    expanded = report(23)
    expanded.providers[0].meta["quota"]["windows"] = [{"key": "weekly", "used_percent": 57}]
    assert not active_report(expanded, activity, now=10100).providers
