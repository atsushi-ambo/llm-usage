"""Burn-rate projection: will this quota hit 100% before reset?"""

from datetime import datetime, timedelta, timezone

from llm_usage.burnrate import (
    enrich_quota_dict,
    infer_window_seconds,
    project_burn,
    projection_to_dict,
)
from llm_usage.menubar_core import burn_of, display_quota, title_quota_chip
from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.serialize import report_to_dict
from llm_usage.models import AggregateReport
from datetime import date


NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_infer_window_from_label():
    assert infer_window_seconds(label="5-hour limit") == 5 * 3600
    assert infer_window_seconds(label="Weekly window") == 7 * 86400
    assert infer_window_seconds(label="14-day window") == 14 * 86400
    assert infer_window_seconds(window_key="five_hour") == 5 * 3600


def test_infer_window_from_period_bounds():
    start = NOW - timedelta(days=3)
    end = NOW + timedelta(days=4)
    secs = infer_window_seconds(period_start=start.isoformat(), resets_at=end.isoformat())
    assert secs is not None
    assert abs(secs - 7 * 86400) < 1


def test_exhausted():
    p = project_burn(100, resets_at=NOW + timedelta(hours=2), now=NOW)
    assert p is not None
    assert p.hits_label == "exhausted"
    assert p.remaining_percent == 0


def test_idle_zero_usage():
    p = project_burn(
        0,
        resets_at=NOW + timedelta(hours=3),
        window_seconds=5 * 3600,
        now=NOW,
    )
    assert p is not None
    assert p.hits_label == "idle"
    assert p.hits_at is None


def test_hits_before_reset_on_5h_window():
    # 2h into a 5h window at 80% → burns 40%/h → remaining 20% → 0.5h → hits soon
    resets = NOW + timedelta(hours=3)
    p = project_burn(
        80,
        resets_at=resets.isoformat(),
        window_seconds=5 * 3600,
        window_label="5-hour limit",
        now=NOW,
    )
    assert p is not None
    assert p.hits_before_reset is True
    assert p.hits_at is not None
    assert p.hits_at < resets
    assert "hits in" in p.hits_label or p.hits_label == "exhausted"


def test_ok_till_reset_when_pace_is_fine():
    # 6 days into a 7-day window at only 10% used → way under pace for 100%
    resets = NOW + timedelta(days=1)
    p = project_burn(
        10.0,
        resets_at=resets.isoformat(),
        window_seconds=7 * 86400,
        window_label="Weekly limit",
        now=NOW,
    )
    assert p is not None
    # ~10% over 6 days ≈ 1.7%/day → remaining 90% needs ~54 days ≫ 1 day left
    assert p.hits_before_reset is False
    assert p.hits_label == "ok till reset"


def test_period_start_high_confidence():
    start = NOW - timedelta(days=2)
    end = NOW + timedelta(days=5)
    p = project_burn(
        50,
        period_start=start.isoformat(),
        resets_at=end.isoformat(),
        now=NOW,
    )
    assert p is not None
    assert p.confidence == "high"
    assert p.pct_per_day is not None
    # 50% over 2 days ≈ 25%/day → remaining 50% ≈ 2 days
    assert p.hours_to_exhaustion is not None
    assert 40 < p.hours_to_exhaustion < 60


def test_projection_to_dict_roundtrip_keys():
    p = project_burn(
        40,
        resets_at=(NOW + timedelta(hours=2)).isoformat(),
        window_seconds=5 * 3600,
        now=NOW,
    )
    d = projection_to_dict(p)
    assert "hits_label" in d
    assert "summary" in d
    assert d["used_percent"] == 40


def test_enrich_quota_dict_adds_burn_and_window_burns():
    q = {
        "used_percent": 60.0,
        "label": "5-hour limit",
        "resets_at": (NOW + timedelta(hours=2)).isoformat(),
        "window_seconds": 5 * 3600,
        "windows": [
            {
                "key": "five_hour",
                "label": "5-hour",
                "used_percent": 60.0,
                "resets_at": (NOW + timedelta(hours=2)).isoformat(),
                "window_seconds": 5 * 3600,
            }
        ],
    }
    # Freeze time via project_from_quota path — enrich uses now=None (live clock).
    # So call project_burn directly for windows; for enrich, just check structure
    # with a known-safe path using project_from_quota.
    enriched = enrich_quota_dict(q, now=NOW)
    assert "burn" in enriched
    assert enriched["burn"]["hits_label"]
    assert enriched["windows"][0]["burn"]["used_percent"] == 60.0


def test_report_to_dict_attaches_burn():
    report = AggregateReport(
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 10),
        providers=[
            ProviderReport(
                provider=ProviderId.GROK,
                display_name="Grok",
                source=SourceKind.SUBSCRIPTION,
                meta={
                    "quota": {
                        "used_percent": 80.0,
                        "label": "Weekly limit",
                        "period_start": (NOW - timedelta(days=6)).isoformat(),
                        "resets_at": (NOW + timedelta(days=1)).isoformat(),
                    }
                },
            )
        ],
    )
    data = report_to_dict(report)
    burn = data["providers"][0]["meta"]["quota"]["burn"]
    assert burn["hits_label"]
    assert "summary" in burn


def test_title_quota_chip_shows_hits_when_before_reset():
    p = ProviderReport(
        provider=ProviderId.GROK,
        display_name="Grok",
        source=SourceKind.SUBSCRIPTION,
        meta={
            "quota": {
                "used_percent": 90.0,
                "label": "Weekly limit",
                "period_start": (NOW - timedelta(days=1)).isoformat(),
                "resets_at": (NOW + timedelta(days=6)).isoformat(),
            }
        },
    )
    # 90% in 1 day → will hit 100% well before 6-day reset
    # burn_of uses live now — for deterministic test use project_from_quota
    from llm_usage.burnrate import project_from_quota as pfq

    proj = pfq(p.meta["quota"], now=NOW)
    assert proj is not None
    assert proj.hits_before_reset is True

    # title_quota_chip uses live clock; just assert shape with a frozen-style
    # quota that is already exhausted so label is stable
    p2 = ProviderReport(
        provider=ProviderId.GROK,
        display_name="Grok",
        source=SourceKind.SUBSCRIPTION,
        meta={"quota": {"used_percent": 100.0, "label": "Weekly limit"}},
    )
    chip = title_quota_chip(p2)
    assert chip == "Grok 100%"


def test_claude_display_quota_carries_window_seconds_for_burn():
    p = ProviderReport(
        provider=ProviderId.CLAUDE,
        display_name="Claude Code",
        source=SourceKind.SUBSCRIPTION,
        meta={
            "quota": {
                "used_percent": 10.0,
                "label": "7-day limit",
                "windows": [
                    {
                        "key": "five_hour",
                        "label": "5-hour",
                        "used_percent": 70.0,
                        "resets_at": (NOW + timedelta(hours=2)).isoformat(),
                        "window_seconds": 5 * 3600,
                    }
                ],
            }
        },
    )
    q = display_quota(p)
    assert q is not None
    assert q["used_percent"] == 70.0
    assert q.get("window_seconds") == 5 * 3600
    burn = burn_of(p)
    # burn_of uses wall clock; with window_seconds we at least get a projection
    assert burn is not None
