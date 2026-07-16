"""Cursor usage via Admin API (Enterprise) or personal dashboard session token."""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone
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
from llm_usage.providers.base import safe_error_str, safe_float, safe_int


def collect_cursor(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.CURSOR,
        display_name="Cursor",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={"console_url": "https://cursor.com/dashboard"},
    )

    if settings.cursor_api_key:
        try:
            _fill_admin_api(report, settings.cursor_api_key, start, end)
            report.source = SourceKind.API
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Admin API: {safe_error_str(exc)}")

    if settings.cursor_session_token and report.source == SourceKind.UNAVAILABLE:
        try:
            _fill_dashboard_session(report, settings.cursor_session_token, start, end)
            report.source = SourceKind.API
            report.notes.append(
                "Using Cursor session cookie (WorkosCursorSessionToken). "
                "Tokens expire — refresh from browser cookies when needed."
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Dashboard session: {safe_error_str(exc)}")

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "Set CURSOR_API_KEY (Enterprise Admin API) or CURSOR_SESSION_TOKEN "
            "(WorkosCursorSessionToken cookie from cursor.com). "
            "Otherwise open https://cursor.com/dashboard for usage."
        )

    return report


def _basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return f"Basic {token}"


def _fill_admin_api(
    report: ProviderReport, api_key: str, start: date, end: date
) -> None:
    headers = {
        "Authorization": _basic_auth_header(api_key),
        "Content-Type": "application/json",
        "User-Agent": "llm-usage/0.1.0",
    }
    # Cursor Admin daily usage is a POST with date range
    payload = {
        "startDate": int(
            datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp()
            * 1000
        ),
        "endDate": int(
            datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()
            * 1000
        ),
    }

    with httpx.Client(timeout=30.0) as client:
        # Team spending
        spend_resp = client.post(
            "https://api.cursor.com/teams/spend",
            headers=headers,
            json={},
        )
        if spend_resp.status_code == 200:
            spend = spend_resp.json()
            report.meta["spend"] = spend
            # try common shapes
            total = (
                safe_float(spend.get("totalSpend"))
                or safe_float(spend.get("total_spend"))
                or safe_float(spend.get("hardLimitOverrideDollars"))
            )
            if total is not None:
                report.cost_usd = total
        elif spend_resp.status_code not in (404, 405):
            spend_resp.raise_for_status()

        daily_resp = client.post(
            "https://api.cursor.com/teams/daily-usage-data",
            headers=headers,
            json=payload,
        )
        if daily_resp.status_code == 200:
            body = daily_resp.json()
            _parse_daily_usage(report, body)
        elif daily_resp.status_code == 403:
            raise RuntimeError(
                "Enterprise access required for Admin daily-usage-data"
            )
        else:
            daily_resp.raise_for_status()


def _parse_daily_usage(report: ProviderReport, body: dict[str, Any]) -> None:
    data = body.get("data") or body.get("usage") or body
    if isinstance(data, dict):
        rows = data.get("data") or data.get("days") or data.get("usage") or []
    else:
        rows = data if isinstance(data, list) else []

    daily: list[DailyPoint] = []
    total_reqs = 0
    total_cost = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        day_raw = row.get("date") or row.get("day") or row.get("startDate")
        day: date | None = None
        if isinstance(day_raw, str) and len(day_raw) >= 10:
            try:
                day = date.fromisoformat(day_raw[:10])
            except ValueError:
                day = None
        elif isinstance(day_raw, (int, float)):
            # ms or s
            ts = day_raw / 1000 if day_raw > 1e12 else day_raw
            day = datetime.fromtimestamp(ts, tz=timezone.utc).date()

        reqs = safe_int(
            row.get("totalLinesAdded")  # not ideal but present on some payloads
            or row.get("composerRequests")
            or row.get("chatRequests")
            or row.get("requests")
            or row.get("totalAccepts")
        )
        # Prefer explicit cost fields
        cost = (
            safe_float(row.get("totalCost"))
            or safe_float(row.get("cost"))
            or safe_float(row.get("spend"))
        )
        # Sum multiple request types when available
        for k in (
            "composerRequests",
            "chatRequests",
            "agentRequests",
            "cmdkUsages",
            "bugbotUsages",
        ):
            if k in row:
                reqs = max(reqs, 0) + safe_int(row.get(k))

        if day:
            daily.append(
                DailyPoint(
                    day=day,
                    requests=reqs,
                    cost_usd=cost,
                    input_tokens=safe_int(row.get("inputTokens") or row.get("input_tokens")),
                    output_tokens=safe_int(
                        row.get("outputTokens") or row.get("output_tokens")
                    ),
                )
            )
        total_reqs += reqs
        if cost:
            total_cost += cost

    if daily:
        report.daily = sorted(daily, key=lambda d: d.day)
        report.requests = total_reqs or sum(d.requests for d in report.daily)
        report.input_tokens = sum(d.input_tokens for d in report.daily)
        report.output_tokens = sum(d.output_tokens for d in report.daily)
        if report.cost_usd is None and total_cost:
            report.cost_usd = total_cost


def _fill_dashboard_session(
    report: ProviderReport, session_token: str, start: date, end: date
) -> None:
    """Use unofficial dashboard endpoints with browser session cookie."""
    cookies = {"WorkosCursorSessionToken": session_token}
    headers = {
        "User-Agent": "llm-usage/0.1.0",
        "Content-Type": "application/json",
    }
    payload = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
    }

    with httpx.Client(timeout=30.0, cookies=cookies, headers=headers) as client:
        # usage summary
        for path in (
            "https://cursor.com/api/dashboard/get-aggregated-usage-events",
            "https://www.cursor.com/api/dashboard/get-aggregated-usage-events",
            "https://cursor.com/api/usage",
            "https://www.cursor.com/api/usage",
        ):
            try:
                resp = client.get(path) if "usage" in path and "aggregated" not in path else client.post(path, json=payload)
                if resp.status_code != 200:
                    continue
                body = resp.json()
                report.meta["raw_dashboard"] = _summarize_dashboard(body)
                _apply_dashboard_body(report, body)
                return
            except Exception:  # noqa: BLE001
                continue

    raise RuntimeError(
        "Could not load Cursor dashboard usage with the given session token"
    )


def _summarize_dashboard(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"type": type(body).__name__}
    keys = list(body.keys())[:20]
    return {"keys": keys}


def _apply_dashboard_body(report: ProviderReport, body: dict[str, Any]) -> None:
    # Flexible parsing — Cursor dashboard shapes evolve
    if "totalCost" in body or "total_cost" in body:
        report.cost_usd = safe_float(body.get("totalCost") or body.get("total_cost"))

    aggregations = (
        body.get("aggregations")
        or body.get("usage")
        or body.get("data")
        or body.get("events")
    )
    models: dict[str, ModelUsage] = {}
    total_cost = 0.0
    total_reqs = 0
    inp = out = 0

    if isinstance(aggregations, list):
        for item in aggregations:
            if not isinstance(item, dict):
                continue
            model = str(
                item.get("model")
                or item.get("modelName")
                or item.get("model_name")
                or "cursor"
            )
            cost = safe_float(item.get("cost") or item.get("totalCost") or item.get("price"))
            i = safe_int(item.get("inputTokens") or item.get("input_tokens"))
            o = safe_int(item.get("outputTokens") or item.get("output_tokens"))
            r = safe_int(item.get("count") or item.get("requests") or item.get("numRequests") or 1)
            mu = models.get(model) or ModelUsage(model=model)
            mu.input_tokens += i
            mu.output_tokens += o
            mu.requests += r
            if cost is not None:
                mu.cost_usd = (mu.cost_usd or 0.0) + cost
                total_cost += cost
            models[model] = mu
            total_reqs += r
            inp += i
            out += o

    if models:
        report.models = sorted(models.values(), key=lambda m: -(m.cost_usd or 0))
        report.requests = total_reqs
        report.input_tokens = inp
        report.output_tokens = out
        if report.cost_usd is None and total_cost:
            report.cost_usd = total_cost

    # Nested summary fields
    for key in ("gpt-4", "gpt-4o", "claude-3-5-sonnet", "claude-4", "premium"):
        if key in body and isinstance(body[key], dict):
            block = body[key]
            num = safe_int(block.get("numRequests") or block.get("requests"))
            max_req = safe_int(block.get("maxRequestUsage") or block.get("max_requests"))
            report.meta.setdefault("plan_usage", {})[key] = {
                "used": num,
                "max": max_req or None,
            }
            report.requests += num
