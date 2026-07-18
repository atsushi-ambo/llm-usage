from datetime import date

from llm_usage.models import (
    AggregateReport,
    DailyPoint,
    ModelUsage,
    ProviderId,
    ProviderReport,
    SourceKind,
)
from llm_usage.serialize import slim_report_for_menubar


def test_slim_report_drops_models_daily_and_raw_meta():
    full = AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 18),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude Code",
                source=SourceKind.SUBSCRIPTION,
                input_tokens=1000,
                output_tokens=200,
                requests=5,
                cost_usd=1.23,
                models=[ModelUsage(model="claude-sonnet", requests=5, input_tokens=1000)],
                daily=[DailyPoint(day=date(2026, 7, 10), requests=5, input_tokens=1000)],
                meta={
                    "quota": {
                        "used_percent": 21.0,
                        "label": "5-hour limit",
                        "plan": "pro",
                        "windows": [
                            {
                                "key": "five_hour",
                                "label": "5-hour",
                                "used_percent": 21.0,
                                "used": 999,
                                "limit": 1000,
                            }
                        ],
                    },
                    "subscription": {"huge": "oauth body" * 100},
                    "plan_type": "pro",
                    "sessions": 42,
                },
                notes=["a long note"],
                errors=["e1", "e2", "e3"],
            )
        ],
    )
    slim = slim_report_for_menubar(full)
    p = slim.providers[0]
    assert p.models == []
    assert p.daily == []
    assert p.notes == []
    assert p.errors == ["e1", "e2"]
    assert p.cost_usd == 1.23
    assert p.requests == 5
    assert "subscription" not in p.meta
    assert "sessions" not in p.meta
    assert p.meta["quota"]["used_percent"] == 21.0
    # window stripped to compact keys only
    assert p.meta["quota"]["windows"][0] == {
        "key": "five_hour",
        "label": "5-hour",
        "used_percent": 21.0,
    }
    assert p.meta["plan_type"] == "pro"
