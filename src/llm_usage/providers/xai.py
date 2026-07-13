"""xAI / Grok usage collector.

xAI's public Management API manages keys/models but does not currently expose a
documented usage/billing time-series endpoint. We validate credentials, list
keys when possible, and surface console links + any residual info.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def collect_xai(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.GROK,
        display_name="Grok / xAI",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={
            "console_url": "https://console.x.ai/team/default/usage",
            "billing_url": "https://console.x.ai/team/default/billing",
        },
    )

    if settings.xai_management_key and settings.xai_team_id:
        try:
            info = _list_team_keys(settings.xai_management_key, settings.xai_team_id)
            report.meta["api_keys"] = info
            report.source = SourceKind.API
            report.notes.append(
                "Management API connected (keys listed). Detailed token/cost "
                "time-series is only in the xAI Console Usage Explorer for now."
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Management API: {exc}")

    if settings.xai_api_key:
        try:
            models = _list_models(settings.xai_api_key)
            report.meta["available_models"] = models
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.API
            report.notes.append(
                f"API key valid — {len(models)} model(s) available. "
                "Usage totals: see console.x.ai Usage Explorer."
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"xAI API: {exc}")

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "Set XAI_API_KEY and/or XAI_MANAGEMENT_KEY + XAI_TEAM_ID. "
            "Full spend charts: https://console.x.ai/team/default/usage"
        )

    return report


def _list_models(api_key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=20.0) as client:
        resp = client.get("https://api.x.ai/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    models = data.get("data") or data.get("models") or []
    names: list[str] = []
    for m in models:
        if isinstance(m, dict):
            names.append(str(m.get("id") or m.get("name") or m))
        else:
            names.append(str(m))
    return names


def _list_team_keys(mgmt_key: str, team_id: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {mgmt_key}"}
    url = f"https://management-api.x.ai/auth/teams/{team_id}/api-keys"
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(url, headers=headers, params={"pageSize": 50})
        resp.raise_for_status()
        body = resp.json()
    keys = body.get("apiKeys") or body.get("api_keys") or body.get("data") or []
    simplified = []
    for k in keys:
        if not isinstance(k, dict):
            continue
        simplified.append(
            {
                "name": k.get("name"),
                "id": k.get("apiKeyId") or k.get("id"),
                "qpm": k.get("qpm"),
                "qps": k.get("qps"),
            }
        )
    return {"count": len(simplified), "keys": simplified}
