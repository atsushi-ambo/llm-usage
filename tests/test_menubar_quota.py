from llm_usage.menubar import (
    _bar_segments,
    _brighten,
    _display_quota,
    _pct_rgb,
    _quota_of,
    _unicode_bar,
    PROVIDER_STYLE,
    _RGB_CRIT,
    _RGB_HOT,
    _RGB_WARN,
)
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


def test_bar_segments_fill_count_matches_percent():
    segs = _bar_segments(50.0, width=10, brand=(100, 100, 200))
    assert len(segs) == 10
    filled = sum(1 for ch, _ in segs if ch == "█")
    empty = sum(1 for ch, _ in segs if ch == "░")
    assert filled == 5
    assert empty == 5


def test_unicode_bar_still_available_as_plain_fallback():
    assert _unicode_bar(0, 5) == "░░░░░"
    assert _unicode_bar(100, 5) == "█████"


def test_pct_rgb_heat_ramp():
    brand = PROVIDER_STYLE["claude"]["rgb"]
    assert _pct_rgb(10, brand) == brand
    assert _pct_rgb(49, brand) == brand
    assert _pct_rgb(50, brand) == _RGB_WARN
    assert _pct_rgb(70, brand) == _RGB_HOT
    assert _pct_rgb(90, brand) == _RGB_CRIT


def test_vscode_palette_values():
    assert PROVIDER_STYLE["claude"]["rgb"] == (206, 145, 120)
    assert PROVIDER_STYLE["codex"]["rgb"] == (106, 153, 85)
    assert PROVIDER_STYLE["grok"]["rgb"] == (197, 134, 192)


def test_brighten_lifts_but_caps_at_255():
    assert _brighten((100, 100, 100), 1.18) == (118, 118, 118)
    assert _brighten((240, 240, 240), 1.18) == (255, 255, 255)
