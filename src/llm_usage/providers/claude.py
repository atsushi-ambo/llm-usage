"""Claude / Anthropic usage: local Claude Code logs + Admin API + OAuth quota."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
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
from llm_usage.pricing import estimate_cost
from llm_usage.providers.base import parse_iso_date, safe_float, safe_int


def collect_claude(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.CLAUDE,
        display_name="Claude / Anthropic",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
    )

    # 1) Admin Usage + Cost API (most accurate for Console org)
    if settings.anthropic_admin_key:
        try:
            _fill_from_admin_api(report, settings.anthropic_admin_key, start, end)
            report.source = SourceKind.API
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Admin API: {exc}")

    # 2) Local Claude Code transcripts (always useful; fills gaps)
    local = _scan_local_logs(settings.claude_projects_dir, start, end)
    if local["requests"] > 0:
        if report.source == SourceKind.UNAVAILABLE:
            report.source = SourceKind.LOCAL_LOGS
            _apply_local(report, local)
            report.notes.append(
                "Estimated from local Claude Code session logs "
                f"({settings.claude_projects_dir})"
            )
        else:
            report.meta["local_tokens"] = local["total_tokens"]
            report.meta["local_requests"] = local["requests"]
            report.notes.append(
                f"Local Claude Code logs: {local['requests']:,} msgs, "
                f"{local['total_tokens']:,} tokens (estimate)"
            )
            if report.models:
                pass
            else:
                report.models = local["models"]
                report.daily = local["daily"]

    # 3) Subscription / rate-limit quota (claude.ai / Claude Code OAuth)
    oauth_token = _read_claude_oauth_token(settings.claude_credentials_path)
    if oauth_token:
        try:
            quota = _fetch_oauth_usage(oauth_token)
            report.meta["subscription"] = quota
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.SUBSCRIPTION
            report.notes.append("Claude subscription quota attached (OAuth usage API)")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"OAuth usage: {exc}")

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "No Claude data found. Log in with Claude Code, or set ANTHROPIC_ADMIN_KEY "
            "for Console usage reports."
        )
        report.meta["console_url"] = "https://console.anthropic.com/settings/usage"

    return report


def _apply_local(report: ProviderReport, local: dict[str, Any]) -> None:
    report.input_tokens = local["input_tokens"]
    report.output_tokens = local["output_tokens"]
    report.cache_read_tokens = local["cache_read_tokens"]
    report.cache_write_tokens = local["cache_write_tokens"]
    report.requests = local["requests"]
    report.cost_usd = local["cost_usd"]
    report.models = local["models"]
    report.daily = local["daily"]
    report.meta["sessions"] = local["sessions"]
    report.meta["estimated"] = True


def _scan_local_logs(root: Path, start: date, end: date) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
        "total_tokens": 0,
        "sessions": 0,
        "models": [],
        "daily": [],
    }
    if not root.exists():
        return empty

    by_model: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "requests": 0,
            "cost_usd": 0.0,
        }
    )
    by_day: dict[date, dict[str, int | float]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    )
    sessions = 0
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
    }

    for path in root.rglob("*.jsonl"):
        sessions += 1
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    _accumulate_row(row, start, end, by_model, by_day, totals)
        except OSError:
            continue

    models = [
        ModelUsage(
            model=name,
            input_tokens=int(m["input_tokens"]),
            output_tokens=int(m["output_tokens"]),
            cache_read_tokens=int(m["cache_read_tokens"]),
            cache_write_tokens=int(m["cache_write_tokens"]),
            requests=int(m["requests"]),
            cost_usd=float(m["cost_usd"]) if m["cost_usd"] else None,
        )
        for name, m in sorted(by_model.items(), key=lambda x: -x[1]["requests"])
    ]
    daily = [
        DailyPoint(
            day=d,
            input_tokens=int(v["input_tokens"]),
            output_tokens=int(v["output_tokens"]),
            requests=int(v["requests"]),
            cost_usd=float(v["cost_usd"]) if v["cost_usd"] else None,
        )
        for d, v in sorted(by_day.items())
    ]
    total_tokens = (
        totals["input_tokens"]
        + totals["output_tokens"]
        + totals["cache_read_tokens"]
        + totals["cache_write_tokens"]
    )
    return {
        **totals,
        "total_tokens": total_tokens,
        "sessions": sessions,
        "models": models,
        "daily": daily,
    }


def _accumulate_row(
    row: dict[str, Any],
    start: date,
    end: date,
    by_model: dict[str, dict[str, int | float]],
    by_day: dict[date, dict[str, int | float]],
    totals: dict[str, int | float],
) -> None:
    msg = row.get("message")
    if not isinstance(msg, dict):
        return
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return

    day = parse_iso_date(row.get("timestamp")) or parse_iso_date(msg.get("created_at"))
    if day is None or day < start or day > end:
        return

    model = str(msg.get("model") or row.get("model") or "unknown")
    if model == "<synthetic>":
        return

    inp = safe_int(usage.get("input_tokens"))
    out = safe_int(usage.get("output_tokens"))
    cache_r = safe_int(usage.get("cache_read_input_tokens"))
    cache_w = safe_int(usage.get("cache_creation_input_tokens"))
    if not any((inp, out, cache_r, cache_w)):
        return

    cost = estimate_cost(model, inp, out, cache_r, cache_w) or 0.0

    m = by_model[model]
    m["input_tokens"] += inp
    m["output_tokens"] += out
    m["cache_read_tokens"] += cache_r
    m["cache_write_tokens"] += cache_w
    m["requests"] += 1
    m["cost_usd"] += cost

    d = by_day[day]
    d["input_tokens"] += inp
    d["output_tokens"] += out
    d["requests"] += 1
    d["cost_usd"] += cost

    totals["input_tokens"] += inp
    totals["output_tokens"] += out
    totals["cache_read_tokens"] += cache_r
    totals["cache_write_tokens"] += cache_w
    totals["requests"] += 1
    totals["cost_usd"] += cost


def _fill_from_admin_api(
    report: ProviderReport, admin_key: str, start: date, end: date
) -> None:
    headers = {
        "x-api-key": admin_key,
        "anthropic-version": "2023-06-01",
        "User-Agent": "llm-usage/0.1.0",
    }
    start_iso = datetime(start.year, start.month, start.day, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    # exclusive end: day after end
    end_exclusive = date.fromordinal(end.toordinal() + 1)
    end_iso = datetime(
        end_exclusive.year, end_exclusive.month, end_exclusive.day, tzinfo=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with httpx.Client(timeout=30.0) as client:
        # Usage report
        usage_params: dict[str, Any] = {
            "starting_at": start_iso,
            "ending_at": end_iso,
            "bucket_width": "1d",
            "group_by[]": "model",
        }
        usage_data = _paginate(
            client,
            "https://api.anthropic.com/v1/organizations/usage_report/messages",
            headers,
            usage_params,
        )
        _merge_usage_buckets(report, usage_data)

        # Cost report
        cost_params = {
            "starting_at": start_iso,
            "ending_at": end_iso,
            "bucket_width": "1d",
            "group_by[]": "description",
        }
        try:
            cost_data = _paginate(
                client,
                "https://api.anthropic.com/v1/organizations/cost_report",
                headers,
                cost_params,
            )
            total_cents = 0.0
            for page in cost_data:
                for bucket in page.get("data") or []:
                    for result in bucket.get("results") or []:
                        # amount is decimal string in cents
                        amount = safe_float(result.get("amount"))
                        if amount is not None:
                            total_cents += amount
            if total_cents:
                report.cost_usd = total_cents / 100.0
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Cost API: {exc}")


def _paginate(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        q = dict(params)
        if page_token:
            q["page"] = page_token
        resp = client.get(url, headers=headers, params=q)
        resp.raise_for_status()
        body = resp.json()
        pages.append(body)
        if not body.get("has_more"):
            break
        page_token = body.get("next_page")
        if not page_token:
            break
    return pages


def _merge_usage_buckets(report: ProviderReport, pages: list[dict[str, Any]]) -> None:
    by_model: dict[str, ModelUsage] = {}
    by_day: dict[date, DailyPoint] = {}

    for page in pages:
        for bucket in page.get("data") or []:
            bucket_start = parse_iso_date(bucket.get("starting_at"))
            for result in bucket.get("results") or []:
                model = str(result.get("model") or "unknown")
                inp = safe_int(result.get("uncached_input_tokens")) + safe_int(
                    result.get("input_tokens")
                )
                # Anthropic reports cache tokens separately
                cache_r = safe_int(result.get("cache_read_input_tokens"))
                cache_w = (
                    safe_int(result.get("cache_creation_input_tokens"))
                    + safe_int(
                        (result.get("cache_creation") or {}).get("ephemeral_1h_input_tokens")
                    )
                    + safe_int(
                        (result.get("cache_creation") or {}).get("ephemeral_5m_input_tokens")
                    )
                )
                out = safe_int(result.get("output_tokens"))
                reqs = safe_int(result.get("num_model_requests") or result.get("request_count"))

                mu = by_model.get(model) or ModelUsage(model=model)
                mu.input_tokens += inp
                mu.output_tokens += out
                mu.cache_read_tokens += cache_r
                mu.cache_write_tokens += cache_w
                mu.requests += reqs or (1 if (inp or out) else 0)
                est = estimate_cost(model, inp, out, cache_r, cache_w)
                if est is not None:
                    mu.cost_usd = (mu.cost_usd or 0.0) + est
                by_model[model] = mu

                if bucket_start:
                    dp = by_day.get(bucket_start) or DailyPoint(day=bucket_start)
                    dp.input_tokens += inp
                    dp.output_tokens += out
                    dp.requests += reqs or (1 if (inp or out) else 0)
                    if est is not None:
                        dp.cost_usd = (dp.cost_usd or 0.0) + est
                    by_day[bucket_start] = dp

    report.models = sorted(by_model.values(), key=lambda m: -m.total_tokens)
    report.daily = sorted(by_day.values(), key=lambda d: d.day)
    report.input_tokens = sum(m.input_tokens for m in report.models)
    report.output_tokens = sum(m.output_tokens for m in report.models)
    report.cache_read_tokens = sum(m.cache_read_tokens for m in report.models)
    report.cache_write_tokens = sum(m.cache_write_tokens for m in report.models)
    report.requests = sum(m.requests for m in report.models)
    if report.cost_usd is None:
        est_total = sum(m.cost_usd or 0.0 for m in report.models)
        if est_total:
            report.cost_usd = est_total
            report.meta["estimated"] = True


def _read_claude_oauth_token(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # common shapes
    for key in ("claudeAiOauth", "oauth", "accessToken", "access_token"):
        if key in data:
            val = data[key]
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                token = val.get("accessToken") or val.get("access_token")
                if token:
                    return str(token)
    # nested credentials
    if isinstance(data.get("claudeAiOauth"), dict):
        t = data["claudeAiOauth"].get("accessToken")
        if t:
            return str(t)
    return None


def _fetch_oauth_usage(token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "llm-usage/0.1.0",
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.get("https://api.anthropic.com/api/oauth/usage", headers=headers)
        if resp.status_code == 401:
            raise RuntimeError("OAuth token rejected (401) — re-login to Claude Code")
        if resp.status_code == 404:
            raise RuntimeError("OAuth usage not available for this account")
        resp.raise_for_status()
        return resp.json()
