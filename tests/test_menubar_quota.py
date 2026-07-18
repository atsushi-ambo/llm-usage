from llm_usage.menubar import _display_quota, _quota_of
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def _claude_report(**quota_overrides) -> ProviderReport:
    quota = {
        "used_percent": 7.0,
        "label": "7-day limit",
        "plan": "pro",
        "resets_at": "2026-07-23T00:00:00Z",
        "windows": [
            {
                "key": "five_hour",
                "label": "5-hour",
                "used_percent": 65.0,
                "resets_at": "2026-07-17T00:59:00Z",
            },
            {
                "key": "seven_day",
                "label": "7-day",
                "used_percent": 7.0,
                "resets_at": "2026-07-23T00:00:00Z",
            },
        ],
    }
    quota.update(quota_overrides)
    return ProviderReport(
        provider=ProviderId.CLAUDE,
        display_name="Claude Code",
        source=SourceKind.SUBSCRIPTION,
        meta={"quota": quota},
    )


def test_claude_menubar_shows_five_hour_not_seven_day():
    p = _claude_report()
    assert _quota_of(p) == 65.0


def test_claude_display_quota_uses_five_hour_reset_time():
    p = _claude_report()
    dq = _display_quota(p)
    assert dq["resets_at"] == "2026-07-17T00:59:00Z"
    assert dq["label"] == "5-hour"
    assert dq["used_percent"] == 65.0


def test_claude_falls_back_to_primary_when_no_five_hour_window():
    p = _claude_report(windows=[{"key": "seven_day", "label": "7-day", "used_percent": 7.0}])
    assert _quota_of(p) == 7.0


def test_non_claude_provider_uses_its_own_primary_unmodified():
    p = ProviderReport(
        provider=ProviderId.GROK,
        display_name="Grok Build / xAI",
        source=SourceKind.LOCAL_LOGS,
        meta={"quota": {"used_percent": 46.0, "label": "Weekly limit"}},
    )
    assert _quota_of(p) == 46.0
    assert _display_quota(p)["used_percent"] == 46.0


def test_no_quota_returns_none():
    p = ProviderReport(
        provider=ProviderId.CURSOR,
        display_name="Cursor",
        source=SourceKind.UNAVAILABLE,
    )
    assert _quota_of(p) is None
    assert _display_quota(p) is None
