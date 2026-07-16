"""Weekly aggregation and sparkline helpers for usage history/trends.

No new persistence here — the per-file log-scan cache (llm_usage.logcache)
already keeps full history cheaply available; this just reshapes whatever
`ProviderReport.daily` a collection run already produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from llm_usage.models import DailyPoint

SPARK_CHARS = "▁▂▃▄▅▆▇█"


@dataclass
class WeekBucket:
    week_start: date
    total_tokens: int
    cost_usd: float | None
    requests: int


def week_start_of(d: date) -> date:
    """Monday of the week containing `d`."""
    return d - timedelta(days=d.weekday())


def weekly_buckets(daily: list[DailyPoint]) -> list[WeekBucket]:
    """Aggregate DailyPoints into Monday-start week buckets, sorted ascending."""
    buckets: dict[date, dict] = {}
    for dp in daily:
        wk = week_start_of(dp.day)
        b = buckets.setdefault(
            wk, {"tokens": 0, "cost": 0.0, "requests": 0, "has_cost": False}
        )
        b["tokens"] += (
            dp.input_tokens + dp.output_tokens + dp.cache_read_tokens + dp.cache_write_tokens
        )
        b["requests"] += dp.requests
        if dp.cost_usd is not None:
            b["cost"] += dp.cost_usd
            b["has_cost"] = True

    return [
        WeekBucket(
            week_start=wk,
            total_tokens=v["tokens"],
            cost_usd=v["cost"] if v["has_cost"] else None,
            requests=v["requests"],
        )
        for wk, v in sorted(buckets.items())
    ]


def daily_totals(daily: list[DailyPoint], start: date, end: date) -> list[int]:
    """One total-token value per day in [start, end], 0 for days with no data."""
    by_day = {
        dp.day: dp.input_tokens + dp.output_tokens + dp.cache_read_tokens + dp.cache_write_tokens
        for dp in daily
    }
    out = []
    cur = start
    while cur <= end:
        out.append(by_day.get(cur, 0))
        cur += timedelta(days=1)
    return out


def sparkline(values: list[float]) -> str:
    """Render a compact unicode sparkline for a series of non-negative values."""
    if not values:
        return ""
    max_v = max(values)
    if max_v <= 0:
        return SPARK_CHARS[0] * len(values)
    n = len(SPARK_CHARS)
    return "".join(SPARK_CHARS[min(n - 1, int((v / max_v) * (n - 1)))] for v in values)


def week_over_week_pct(current: float, previous: float) -> float | None:
    """% change from previous to current week; None if previous is 0 (undefined)."""
    if previous <= 0:
        return None
    return ((current - previous) / previous) * 100.0
