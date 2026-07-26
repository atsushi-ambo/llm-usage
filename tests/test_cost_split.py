from datetime import date

from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind


def test_billed_vs_estimated_cost_split():
    report = AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        providers=[
            ProviderReport(
                provider=ProviderId.OPENAI,
                display_name="OpenAI",
                source=SourceKind.API,
                cost_usd=10.0,
                meta={},
            ),
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude",
                source=SourceKind.LOCAL_LOGS,
                cost_usd=3.0,
                meta={"estimated": True},
            ),
            ProviderReport(
                provider=ProviderId.GEMINI,
                display_name="Gemini",
                source=SourceKind.UNAVAILABLE,
                cost_usd=None,
            ),
        ],
    )
    assert report.total_cost_usd == 13.0
    assert report.billed_cost_usd == 10.0
    assert report.estimated_cost_usd == 3.0
    assert report.has_estimated_cost is True


def test_only_estimated_marks_total():
    report = AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude",
                source=SourceKind.LOCAL_LOGS,
                cost_usd=2.5,
                meta={"estimated": True},
            )
        ],
    )
    assert report.billed_cost_usd is None
    assert report.estimated_cost_usd == 2.5
    assert report.has_estimated_cost is True
