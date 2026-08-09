"""Tests for the OpenAI org usage/costs collector.

Covers two regressions fixed earlier in this project's history:
  * a cost-endpoint failure used to discard already-fetched usage data,
    because `_apply_usage` ran after `_fetch_costs` in the same try block;
  * daily points dropped cache-read tokens while report totals counted
    them, so per-day and total token sums disagreed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

import llm_usage.providers.openai_provider as openai_module
from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.providers.openai_provider import _apply_usage, _fetch_costs, collect_openai

START = date(2026, 7, 1)
END = date(2026, 7, 7)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def _blank_report() -> ProviderReport:
    return ProviderReport(
        provider=ProviderId.OPENAI,
        display_name="OpenAI",
        source=SourceKind.UNAVAILABLE,
    )


def _bucket(day: date, results: list[dict]) -> dict:
    ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    return {"start_time": ts, "results": results}


# ── collect_openai wiring ─────────────────────────────────────────────


def test_no_key_returns_unavailable_with_setup_hint():
    report = collect_openai(_settings(), START, END)
    assert report.source == SourceKind.UNAVAILABLE
    assert not report.errors
    assert any("OPENAI_ADMIN_KEY" in n for n in report.notes)


def test_cost_failure_does_not_discard_usage(monkeypatch):
    """A 429/5xx on /costs must not throw away usage we already fetched."""
    monkeypatch.setattr(
        openai_module,
        "_fetch_completions_usage",
        lambda c, h, s, days: [
            _bucket(START, [{"model": "gpt-4o", "input_tokens": 100, "output_tokens": 20,
                             "num_model_requests": 3}])
        ],
    )

    def _boom(client, headers, start_ts, days):
        request = httpx.Request("GET", "https://api.openai.com/v1/organization/costs")
        response = httpx.Response(429, text="slow down", request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr(openai_module, "_fetch_costs", _boom)

    report = collect_openai(_settings(OPENAI_ADMIN_KEY="sk-admin-fake"), START, END)

    assert report.source == SourceKind.API
    assert report.input_tokens == 100  # usage survived
    assert report.output_tokens == 20
    assert report.requests == 3
    assert report.cost_usd is None
    assert any("Costs:" in e and "429" in e for e in report.errors)


def test_usage_failure_is_reported_and_leaves_provider_unavailable(monkeypatch):
    def _boom(client, headers, start_ts, days):
        request = httpx.Request("GET", "https://api.openai.com/v1/organization/usage/completions")
        response = httpx.Response(401, text="bad key", request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(openai_module, "_fetch_completions_usage", _boom)
    report = collect_openai(_settings(OPENAI_API_KEY="sk-fake"), START, END)

    assert report.source == SourceKind.UNAVAILABLE
    assert any("401" in e for e in report.errors)
    assert any("Admin API key" in n for n in report.notes)


def test_plain_api_key_adds_admin_key_hint(monkeypatch):
    monkeypatch.setattr(openai_module, "_fetch_completions_usage", lambda c, h, s, days: [])
    monkeypatch.setattr(openai_module, "_fetch_costs", lambda c, h, s, days: 1.25)

    report = collect_openai(_settings(OPENAI_API_KEY="sk-fake"), START, END)
    assert report.cost_usd == 1.25
    assert any("OPENAI_API_KEY" in n for n in report.notes)


def test_admin_key_is_preferred_over_plain_key(monkeypatch):
    seen: list[str] = []

    def _capture(client, headers, start_ts, days):
        seen.append(headers["Authorization"])
        return []

    monkeypatch.setattr(openai_module, "_fetch_completions_usage", _capture)
    monkeypatch.setattr(openai_module, "_fetch_costs", lambda c, h, s, days: None)

    collect_openai(
        _settings(OPENAI_ADMIN_KEY="sk-admin-fake", OPENAI_API_KEY="sk-plain-fake"),
        START,
        END,
    )
    assert seen == ["Bearer sk-admin-fake"]


# ── _apply_usage aggregation ──────────────────────────────────────────


def test_apply_usage_aggregates_across_buckets_and_models():
    report = _blank_report()
    _apply_usage(
        report,
        [
            _bucket(
                date(2026, 7, 1),
                [
                    {"model": "gpt-4o", "input_tokens": 100, "output_tokens": 10,
                     "num_model_requests": 1},
                    {"model": "o3", "input_tokens": 5, "output_tokens": 1,
                     "num_model_requests": 2},
                ],
            ),
            _bucket(
                date(2026, 7, 2),
                [{"model": "gpt-4o", "input_tokens": 50, "output_tokens": 5,
                  "num_model_requests": 4}],
            ),
        ],
    )

    assert report.input_tokens == 155
    assert report.output_tokens == 16
    assert report.requests == 7
    assert {m.model for m in report.models} == {"gpt-4o", "o3"}
    gpt4o = next(m for m in report.models if m.model == "gpt-4o")
    assert gpt4o.input_tokens == 150  # summed across both days
    assert [d.day for d in report.daily] == [date(2026, 7, 1), date(2026, 7, 2)]


def test_daily_points_include_cache_read_tokens():
    """Report totals count cache reads, so daily points must too."""
    report = _blank_report()
    _apply_usage(
        report,
        [_bucket(START, [{"model": "gpt-4o", "input_tokens": 10, "output_tokens": 2,
                          "input_cached_tokens": 40, "num_model_requests": 1}])],
    )
    assert report.cache_read_tokens == 40
    assert report.daily[0].cache_read_tokens == 40


def test_apply_usage_tolerates_missing_fields_and_unknown_model():
    report = _blank_report()
    _apply_usage(report, [_bucket(START, [{}])])
    assert report.models[0].model == "unknown"
    assert report.input_tokens == 0


def test_apply_usage_skips_daily_when_bucket_has_no_timestamp():
    report = _blank_report()
    _apply_usage(report, [{"results": [{"model": "gpt-4o", "input_tokens": 5}]}])
    assert report.input_tokens == 5
    assert report.daily == []


def test_models_sorted_by_total_tokens_desc():
    report = _blank_report()
    _apply_usage(
        report,
        [_bucket(START, [
            {"model": "small", "input_tokens": 1},
            {"model": "big", "input_tokens": 1000},
        ])],
    )
    assert [m.model for m in report.models] == ["big", "small"]


# ── _fetch_costs response-shape handling ──────────────────────────────


def _costs_client(body: dict, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "amount",
    [
        {"value": 1.5, "currency": "usd"},  # newer object shape
        1.5,  # older numeric shape
    ],
)
def test_fetch_costs_handles_both_amount_shapes(amount):
    body = {"data": [{"results": [{"amount": amount}]}]}
    with _costs_client(body) as client:
        assert _fetch_costs(client, {}, 0, 7) == 1.5


def test_fetch_costs_sums_across_buckets():
    body = {
        "data": [
            {"results": [{"amount": {"value": 1.0}}, {"amount": {"value": 2.0}}]},
            {"results": [{"amount": 0.5}]},
        ]
    }
    with _costs_client(body) as client:
        assert _fetch_costs(client, {}, 0, 7) == 3.5


def test_fetch_costs_returns_none_when_zero():
    """Zero spend is reported as 'unknown' rather than a misleading $0.00."""
    with _costs_client({"data": [{"results": [{"amount": 0}]}]}) as client:
        assert _fetch_costs(client, {}, 0, 7) is None


@pytest.mark.parametrize("status", [401, 403, 404])
def test_fetch_costs_treats_missing_scope_as_no_data_not_error(status):
    """A plain API key lacks the costs scope; that's expected, not a failure."""
    with _costs_client({}, status=status) as client:
        assert _fetch_costs(client, {}, 0, 7) is None


def test_fetch_costs_raises_on_server_error():
    with _costs_client({}, status=500) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_costs(client, {}, 0, 7)
