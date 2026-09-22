from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.providers.cursor import _apply_dashboard_body, _parse_daily_usage


def _empty_report() -> ProviderReport:
    return ProviderReport(
        provider=ProviderId.CURSOR,
        display_name="Cursor",
        source=SourceKind.UNAVAILABLE,
    )


def test_daily_usage_does_not_double_count_request_types():
    # A row that has both a generic "requests" field AND broken-out
    # per-type fields should only count the per-type sum once, not add the
    # generic field on top of it.
    report = _empty_report()
    body = {
        "data": [
            {
                "date": "2026-07-01",
                "requests": 999,  # would double-count if summed with types below
                "composerRequests": 3,
                "chatRequests": 2,
            }
        ]
    }
    _parse_daily_usage(report, body)
    assert report.requests == 5


def test_daily_usage_falls_back_to_generic_field_when_no_types_present():
    report = _empty_report()
    body = {"data": [{"date": "2026-07-01", "requests": 7}]}
    _parse_daily_usage(report, body)
    assert report.requests == 7


def test_plan_usage_summary_does_not_double_count_aggregation_requests():
    report = _empty_report()
    body = {
        "aggregations": [
            {"model": "gpt-4o", "count": 4, "inputTokens": 10, "outputTokens": 5},
        ],
        "gpt-4o": {"numRequests": 4, "maxRequestUsage": 100},
    }
    _apply_dashboard_body(report, body)
    # Aggregation list already gave us a request count; the legacy
    # per-model summary block must not be added on top of it.
    assert report.requests == 4


def test_plan_usage_used_as_fallback_when_no_aggregations():
    report = _empty_report()
    body = {"gpt-4o": {"numRequests": 12, "maxRequestUsage": 100}}
    _apply_dashboard_body(report, body)
    assert report.requests == 12


def test_total_lines_added_is_never_counted_as_requests():
    # Cursor daily rows sometimes include IDE edit metrics. Those are not
    # model requests and must not inflate the request total.
    report = _empty_report()
    body = {
        "data": [
            {
                "date": "2026-07-01",
                "totalLinesAdded": 5000,
                "totalLinesDeleted": 200,
            }
        ]
    }
    _parse_daily_usage(report, body)
    assert report.requests == 0


def test_claude_oauth_fixture_normalizes_utilization():
    """Fixture-style: Anthropic OAuth utilization is 0–1; we surface 0–100."""
    from llm_usage.quota import claude_quota_from_oauth

    body = {
        "five_hour": {
            "utilization": 0.42,
            "resets_at": 1_786_000_000,
        },
        "seven_day": {
            "utilization": 0.11,
            "resets_at": 1_786_500_000,
        },
    }
    q = claude_quota_from_oauth(body, plan="pro")
    assert q["used_percent"] == 42.0
    assert q["window_seconds"] == 5 * 3600
    assert len(q["windows"]) == 2
    assert q["windows"][0]["key"] == "five_hour"
    assert q["windows"][0]["window_seconds"] == 5 * 3600
