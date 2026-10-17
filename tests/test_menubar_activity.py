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


def test_open_quota_stays_visible_while_usage_remains():
    activity = {}
    assert len(active_report(report(22), activity, now=9900).providers) == 1
    assert len(active_report(report(22), activity, now=9900 + 3 * 3600).providers) == 1
    assert not active_report(report(0), activity, now=9900 + 3 * 3600).providers


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
    assert len(active_report(report(23), activity, now=13000).providers) == 1
    assert activity["codex"]["last_active"] == 0


def test_persistence_and_nonquota_activity():
    import json

    activity = {}
    assert not active_report(report(requests=2), activity, now=9900).providers
    assert len(active_report(report(requests=3), activity, now=10000).providers) == 1
    restored = json.loads(json.dumps(activity))
    assert not active_report(report(requests=3), restored, now=12000).providers
    assert len(active_report(report(requests=4), restored, now=13000).providers) == 1


def test_positive_balance_stays_without_counting_as_fresh_activity():
    activity = {}
    assert len(active_report(report(57), activity, now=10000).providers) == 1
    assert activity["codex"]["last_active"] == 0
    assert len(active_report(report(57), activity, now=10000 + 3 * 3600).providers) == 1
    assert activity["codex"]["last_active"] == 0


def test_migrate_false_initial_activity_without_losing_baseline():
    activity = {"codex": {"readings": {"quota:primary": 57}, "last_active": 9999}}
    assert len(active_report(report(57), activity, now=10000).providers) == 1
    assert activity["codex"]["last_active"] == 0
    assert len(active_report(report(58), activity, now=10100).providers) == 1
    assert activity["codex"]["last_active"] == 10100


def test_stale_cache_increase_is_not_activity():
    activity = {}
    active_report(report(23), activity, now=10000)
    stale = report(57)
    stale.providers[0].meta["quota_stale"] = True
    assert not active_report(stale, activity, now=10100).providers
    assert len(active_report(report(23), activity, now=10200).providers) == 1
    assert activity["codex"]["last_active"] == 0


def test_newly_available_window_only_establishes_baseline():
    activity = {}
    active_report(report(0), activity, now=10000)
    expanded = report(0)
    expanded.providers[0].meta["quota"]["windows"] = [{"key": "weekly", "used_percent": 57}]
    assert len(active_report(expanded, activity, now=10100).providers) == 1
    assert activity["codex"]["last_active"] == 0


def test_codex_remains_visible_until_five_hour_reset():
    from datetime import datetime, timezone

    activity = {}
    codex = report(88)
    codex.providers[0].meta["quota"]["resets_at"] = datetime.fromtimestamp(
        20000, timezone.utc
    ).isoformat()
    assert len(active_report(codex, activity, now=10000).providers) == 1
    assert len(active_report(codex, activity, now=19000).providers) == 1
    assert not active_report(codex, activity, now=20001).providers


def test_stale_quota_stays_visible_only_while_its_reset_is_ahead():
    """A rate-limited provider keeps its last reading until that window resets.

    Hiding every stale reading made Claude vanish on any 429/5xx while Codex
    and Grok stayed on screen. A known future reset proves the reading is
    from the current period; once it passes, the provider drops off.
    """
    from datetime import datetime, timezone

    activity = {}
    codex = report(88)
    codex.providers[0].meta["quota"]["resets_at"] = datetime.fromtimestamp(
        20000, timezone.utc
    ).isoformat()
    codex.providers[0].meta["quota_stale"] = True
    assert len(active_report(codex, activity, now=10000).providers) == 1
    assert not active_report(codex, activity, now=20001).providers


def test_stale_quota_without_reset_time_is_hidden():
    """No reset time means nothing shows the reading isn't from a closed period."""
    activity = {}
    stale = report(88)
    stale.providers[0].meta["quota"].pop("resets_at", None)
    stale.providers[0].meta["quota_stale"] = True
    assert not active_report(stale, activity, now=10000).providers


def grok_report(pct=8.0, *, stale=False, windows=True):
    quota = {"label": "Weekly limit"}
    if pct is not None:
        quota["used_percent"] = pct
    if windows and pct is not None:
        quota["windows"] = [
            {"key": "grokbuild", "label": "GrokBuild", "used_percent": max(0.0, pct - 3)},
            {"key": "grokchat", "label": "GrokChat", "used_percent": min(pct, 3.0)},
        ]
    meta = {"quota": quota}
    if stale:
        meta["quota_stale"] = True
    return AggregateReport(
        period_start=date.today(),
        period_end=date.today(),
        providers=[
            ProviderReport(
                provider=ProviderId.GROK,
                display_name="Grok Build / xAI",
                source=SourceKind.API,
                meta=meta,
            )
        ],
    )


def test_grok_stays_visible_while_weekly_usage_remains():
    activity = {}
    assert len(active_report(grok_report(8), activity, now=10000).providers) == 1
    assert len(active_report(grok_report(8), activity, now=10000 + 3 * 3600).providers) == 1
    assert not active_report(grok_report(0), activity, now=90000).providers


def test_grok_without_quota_or_stale_quota_does_not_stay_up():
    activity = {}
    assert not active_report(grok_report(None, windows=False), activity, now=10000).providers
    assert not active_report(grok_report(8, stale=True), activity, now=10000).providers


def test_weekly_window_keeps_provider_after_five_hour_reset():
    from datetime import datetime, timezone

    activity = {}
    codex = report(0)
    codex.providers[0].meta["quota"]["resets_at"] = datetime.fromtimestamp(
        15000, timezone.utc
    ).isoformat()
    weekly_reset = datetime.fromtimestamp(10000 + 6 * 86400, timezone.utc).isoformat()
    codex.providers[0].meta["quota"]["windows"] = [
        {
            "key": "primary",
            "label": "5-hour",
            "used_percent": 0,
            "resets_at": datetime.fromtimestamp(15000, timezone.utc).isoformat(),
        },
        {
            "key": "secondary",
            "label": "Weekly",
            "used_percent": 14,
            "resets_at": weekly_reset,
        },
    ]
    assert len(active_report(codex, activity, now=20000).providers) == 1
    assert activity["codex"]["last_active"] == 0
    codex.providers[0].meta["quota"]["windows"][1]["used_percent"] = 0
    assert not active_report(codex, activity, now=21000).providers


def test_claude_week_window_stays_after_five_hour_reset():
    from datetime import datetime, timezone

    activity = {}
    claude = AggregateReport(
        period_start=date.today(),
        period_end=date.today(),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude",
                source=SourceKind.SUBSCRIPTION,
                meta={
                    "quota": {
                        "used_percent": 0,
                        "resets_at": datetime.fromtimestamp(15000, timezone.utc).isoformat(),
                        "windows": [
                            {
                                "key": "five_hour",
                                "label": "5-hour",
                                "used_percent": 0,
                                "resets_at": datetime.fromtimestamp(15000, timezone.utc).isoformat(),
                            },
                            {
                                "key": "seven_day",
                                "label": "7-day",
                                "used_percent": 34,
                                "resets_at": datetime.fromtimestamp(
                                    10000 + 5 * 86400, timezone.utc
                                ).isoformat(),
                            },
                        ],
                    }
                },
            )
        ],
    )
    assert len(active_report(claude, activity, now=20000).providers) == 1
