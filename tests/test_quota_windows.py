from llm_usage.quota import quota_windows
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def test_quota_windows_uses_windows_list_when_present():
    p = ProviderReport(
        provider=ProviderId.CLAUDE,
        display_name="Claude Code",
        source=SourceKind.SUBSCRIPTION,
        meta={
            "quota": {
                "used_percent": 7.0,
                "label": "7-day limit",
                "windows": [
                    {"key": "five_hour", "label": "5-hour", "used_percent": 65.0},
                    {"key": "seven_day", "label": "7-day", "used_percent": 7.0},
                ],
            }
        },
    )
    windows = quota_windows(p)
    # Only the windows-list entries — no duplicate from the top-level
    # used_percent, which mirrors one of them.
    assert windows == [("5-hour", 65.0), ("7-day", 7.0)]


def test_quota_windows_falls_back_to_top_level_when_no_windows_list():
    p = ProviderReport(
        provider=ProviderId.CODEX,
        display_name="OpenAI / Codex",
        source=SourceKind.SUBSCRIPTION,
        meta={"quota": {"used_percent": 42.0, "label": "Weekly window"}},
    )
    assert quota_windows(p) == [("Weekly window", 42.0)]


def test_quota_windows_empty_when_no_quota():
    p = ProviderReport(
        provider=ProviderId.CURSOR,
        display_name="Cursor",
        source=SourceKind.UNAVAILABLE,
    )
    assert quota_windows(p) == []
