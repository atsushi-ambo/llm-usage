from datetime import date

from llm_usage.menubar import _quota_crossings
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind


def _report(used_percent: float, label: str = "5-hour") -> AggregateReport:
    p = ProviderReport(
        provider=ProviderId.CLAUDE,
        display_name="Claude Code",
        source=SourceKind.SUBSCRIPTION,
        meta={"quota": {"used_percent": used_percent, "label": label}},
    )
    return AggregateReport(period_start=date(2026, 7, 1), period_end=date(2026, 7, 16), providers=[p])


def test_crossing_70_percent_notifies_once():
    notified: dict = {}
    crossings = _quota_crossings(_report(75.0), notified)
    assert crossings == [("Claude Code", "5-hour", 75.0, 70)]

    # Same level again on the next poll — no repeat notification.
    crossings2 = _quota_crossings(_report(78.0), notified)
    assert crossings2 == []


def test_crossing_90_after_70_notifies_again():
    notified: dict = {}
    _quota_crossings(_report(72.0), notified)
    crossings = _quota_crossings(_report(95.0), notified)
    assert crossings == [("Claude Code", "5-hour", 95.0, 90)]


def test_dropping_back_below_threshold_clears_state_for_a_future_notification():
    notified: dict = {}
    _quota_crossings(_report(95.0), notified)
    # Window resets
    _quota_crossings(_report(5.0), notified)
    assert notified == {}
    # Crosses 70% again after reset — should notify again
    crossings = _quota_crossings(_report(71.0), notified)
    assert crossings == [("Claude Code", "5-hour", 71.0, 70)]


def test_below_lowest_threshold_never_notifies():
    notified: dict = {}
    crossings = _quota_crossings(_report(50.0), notified)
    assert crossings == []
    assert notified == {}
