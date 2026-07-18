"""OpenRouter — pay-as-you-go credit/usage via OPENROUTER_API_KEY.

No local logs to fall back on (OpenRouter is a routing proxy, not a CLI
tool with a session log format), and its key-info endpoint only reports
aggregate spend/limit, not per-model token breakdowns.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.providers.base import safe_error_str, safe_float


def collect_openrouter(
    settings: Settings,
    start: date,
    end: date,
    *,
    quota_only: bool = False,
) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.OPENROUTER,
        display_name="OpenRouter",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={"console_url": "https://openrouter.ai/activity"},
    )

    key = settings.openrouter_api_key
    if not key:
        report.notes.append(
            "Set OPENROUTER_API_KEY for pay-as-you-go credit/usage info."
        )
        return report
    # quota_only is accepted for collector API symmetry; this path is already light.

    try:
        info = _fetch_key_info(key)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"OpenRouter API: {safe_error_str(exc)}")
        return report

    report.source = SourceKind.API
    usage = safe_float(info.get("usage"))
    limit = safe_float(info.get("limit"))
    is_free = info.get("is_free_tier")

    if usage is not None:
        report.cost_usd = usage
    if is_free is not None:
        report.meta["is_free_tier"] = is_free
    if limit is not None and limit > 0:
        pct = 100.0 * (usage or 0.0) / limit
        report.meta["quota"] = {
            "used_percent": max(0.0, min(100.0, pct)),
            "label": "Credit limit",
            "plan": "Free tier" if is_free else "Pay-as-you-go",
            "resets_at": None,
        }

    note = f"OpenRouter · spend ${usage:,.2f}" if usage is not None else "OpenRouter key valid"
    if limit is not None:
        note += f" of ${limit:,.2f} limit"
    elif is_free:
        note += " (free tier)"
    report.notes.append(note)

    rate_limit = info.get("rate_limit")
    if isinstance(rate_limit, dict) and rate_limit:
        report.meta["rate_limit"] = rate_limit

    return report


def _fetch_key_info(api_key: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "llm-usage/0.1.0"}
    with httpx.Client(timeout=20.0) as client:
        resp = client.get("https://openrouter.ai/api/v1/auth/key", headers=headers)
        resp.raise_for_status()
        body = resp.json()
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {}
