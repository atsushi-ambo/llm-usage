from datetime import date

from llm_usage.history import (
    daily_totals,
    sparkline,
    week_over_week_pct,
    week_start_of,
    weekly_buckets,
)
from llm_usage.models import DailyPoint


def test_week_start_of_monday_anchored():
    # 2026-07-16 is a Thursday
    assert week_start_of(date(2026, 7, 16)) == date(2026, 7, 13)
    # Monday maps to itself
    assert week_start_of(date(2026, 7, 13)) == date(2026, 7, 13)


def test_weekly_buckets_aggregates_across_week_boundary():
    daily = [
        DailyPoint(day=date(2026, 7, 13), input_tokens=100, output_tokens=50, cost_usd=1.0),
        DailyPoint(day=date(2026, 7, 15), input_tokens=200, output_tokens=0, cost_usd=2.0),
        DailyPoint(day=date(2026, 7, 20), input_tokens=10, output_tokens=0, cost_usd=None),
    ]
    buckets = weekly_buckets(daily)
    assert len(buckets) == 2
    week1, week2 = buckets
    assert week1.week_start == date(2026, 7, 13)
    assert week1.total_tokens == 350
    assert week1.cost_usd == 3.0
    week2 = buckets[1]
    assert week2.week_start == date(2026, 7, 20)
    assert week2.total_tokens == 10
    # No DailyPoint in this bucket had a cost — should stay None, not 0.0
    assert week2.cost_usd is None


def test_weekly_buckets_sorted_ascending_regardless_of_input_order():
    daily = [
        DailyPoint(day=date(2026, 7, 20), input_tokens=1),
        DailyPoint(day=date(2026, 7, 6), input_tokens=1),
        DailyPoint(day=date(2026, 7, 13), input_tokens=1),
    ]
    buckets = weekly_buckets(daily)
    assert [b.week_start for b in buckets] == [date(2026, 7, 6), date(2026, 7, 13), date(2026, 7, 20)]


def test_daily_totals_fills_gaps_with_zero():
    daily = [DailyPoint(day=date(2026, 7, 1), input_tokens=100, output_tokens=50)]
    totals = daily_totals(daily, date(2026, 6, 29), date(2026, 7, 2))
    assert totals == [0, 0, 150, 0]


def test_sparkline_empty():
    assert sparkline([]) == ""


def test_sparkline_all_zero_uses_lowest_char():
    assert sparkline([0, 0, 0]) == "▁▁▁"


def test_sparkline_scales_to_max():
    s = sparkline([0, 50, 100])
    assert len(s) == 3
    assert s[0] == "▁"
    assert s[-1] == "█"


def test_week_over_week_pct():
    assert week_over_week_pct(150, 100) == 50.0
    assert week_over_week_pct(50, 100) == -50.0
    assert week_over_week_pct(10, 0) is None
