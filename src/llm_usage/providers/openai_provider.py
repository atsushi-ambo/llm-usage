"""OpenAI organization usage + costs API."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

import httpx

from llm_usage.config import Settings
from llm_usage.models import (
    DailyPoint,
    ModelUsage,
    ProviderId,
    ProviderReport,
    SourceKind,
)
from llm_usage.providers.base import safe_float, safe_int


def collect_openai(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.OPENAI,
        display_name="OpenAI",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={"console_url": "https://platform.openai.com/usage"},
    )

    key = settings.openai_admin_key or settings.openai_api_key
    if not key:
        report.notes.append(
            "Set OPENAI_ADMIN_KEY (preferred) or OPENAI_API_KEY for org usage/costs. "
            "Admin key required for /v1/organization/* endpoints."
        )
        return report

    start_ts = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
    # end is inclusive for display; API uses start_time + buckets
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "llm-usage/0.1.0",
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            usage = _fetch_completions_usage(client, headers, start_ts, days=settings.days)
            costs = _fetch_costs(client, headers, start_ts, days=settings.days)
        _apply_usage(report, usage)
        if costs is not None:
            report.cost_usd = costs
        report.source = SourceKind.API
        if not settings.openai_admin_key:
            report.notes.append(
                "Using OPENAI_API_KEY — if you get 401/404, create an Admin key "
                "in platform.openai.com → Organization → Admin keys."
            )
    except httpx.HTTPStatusError as exc:
        report.errors.append(
            f"OpenAI API HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        )
        report.notes.append(
            "Org usage/costs need an Admin API key with usage.read scope."
        )
    except Exception as exc:  # noqa: BLE001
        report.errors.append(str(exc))

    return report


def _fetch_completions_usage(
    client: httpx.Client, headers: dict[str, str], start_ts: int, days: int
) -> list[dict[str, Any]]:
    url = "https://api.openai.com/v1/organization/usage/completions"
    buckets: list[dict[str, Any]] = []
    page: str | None = None
    while True:
        params: dict[str, Any] = {
            "start_time": start_ts,
            "bucket_width": "1d",
            "limit": min(max(days, 1), 31),
            "group_by[]": "model",
        }
        if page:
            params["page"] = page
        resp = client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        buckets.extend(body.get("data") or [])
        if not body.get("has_more"):
            break
        page = body.get("next_page")
        if not page:
            break
    return buckets


def _fetch_costs(
    client: httpx.Client, headers: dict[str, str], start_ts: int, days: int
) -> float | None:
    url = "https://api.openai.com/v1/organization/costs"
    params = {
        "start_time": start_ts,
        "bucket_width": "1d",
        "limit": min(max(days, 1), 180),
    }
    resp = client.get(url, headers=headers, params=params)
    if resp.status_code in (401, 403, 404):
        return None
    resp.raise_for_status()
    body = resp.json()
    total = 0.0
    for bucket in body.get("data") or []:
        for result in bucket.get("results") or []:
            # amount is object {value, currency} or number depending on version
            amount = result.get("amount")
            if isinstance(amount, dict):
                total += safe_float(amount.get("value"), 0.0) or 0.0
            else:
                total += safe_float(amount, 0.0) or 0.0
    return total if total else None


def _apply_usage(report: ProviderReport, buckets: list[dict[str, Any]]) -> None:
    by_model: dict[str, ModelUsage] = {}
    by_day: dict[date, DailyPoint] = {}

    for bucket in buckets:
        start_time = bucket.get("start_time")
        day: date | None = None
        if isinstance(start_time, (int, float)):
            day = datetime.fromtimestamp(start_time, tz=timezone.utc).date()

        for result in bucket.get("results") or []:
            model = str(result.get("model") or "unknown")
            inp = safe_int(result.get("input_tokens"))
            out = safe_int(result.get("output_tokens"))
            reqs = safe_int(result.get("num_model_requests"))
            # input_cached_tokens sometimes present
            cache_r = safe_int(result.get("input_cached_tokens"))

            mu = by_model.get(model) or ModelUsage(model=model)
            mu.input_tokens += inp
            mu.output_tokens += out
            mu.cache_read_tokens += cache_r
            mu.requests += reqs
            by_model[model] = mu

            if day:
                dp = by_day.get(day) or DailyPoint(day=day)
                dp.input_tokens += inp
                dp.output_tokens += out
                dp.requests += reqs
                by_day[day] = dp

    report.models = sorted(by_model.values(), key=lambda m: -m.total_tokens)
    report.daily = sorted(by_day.values(), key=lambda d: d.day)
    report.input_tokens = sum(m.input_tokens for m in report.models)
    report.output_tokens = sum(m.output_tokens for m in report.models)
    report.cache_read_tokens = sum(m.cache_read_tokens for m in report.models)
    report.requests = sum(m.requests for m in report.models)
