"""Gemini usage: local Gemini CLI chat logs + optional API key tier check."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
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
from llm_usage.providers.base import parse_iso_date, safe_int


def collect_gemini(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.GEMINI,
        display_name="Gemini / Google",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={
            "console_url": "https://aistudio.google.com/usage",
            "billing_url": "https://aistudio.google.com/billing",
        },
    )

    local = _scan_local_logs(settings.gemini_home_dir, start, end)
    if local["requests"] > 0:
        report.source = SourceKind.LOCAL_LOGS
        report.input_tokens = local["input_tokens"]
        report.output_tokens = local["output_tokens"]
        report.requests = local["requests"]
        report.cost_usd = local["cost_usd"] or None
        report.models = local["models"]
        report.daily = local["daily"]
        report.meta["estimated"] = True
        report.notes.append(
            f"Estimated from Gemini CLI logs under {settings.gemini_home_dir}"
        )

    if settings.gemini_api_key:
        try:
            models = _list_models(settings.gemini_api_key)
            report.meta["available_models"] = models[:30]
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.API
            report.notes.append(
                f"API key valid — {len(models)} model(s). "
                "Spend dashboard: aistudio.google.com/usage (no public usage time-series API)."
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Gemini API: {exc}")

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "No Gemini CLI logs found and no GEMINI_API_KEY set. "
            "Use AI Studio usage page or install Gemini CLI to accumulate local history."
        )

    return report


def _scan_local_logs(root: Path, start: date, end: date) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
        "models": [],
        "daily": [],
    }
    if not root.exists():
        return empty

    by_model: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "requests": 0,
            "cost_usd": 0.0,
        }
    )
    by_day: dict[date, dict[str, int | float]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}
    )
    totals = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "cost_usd": 0.0}

    # Gemini CLI: ~/.gemini/tmp/*/chats/*.json
    chat_files = list(root.glob("tmp/*/chats/*.json"))
    chat_files += list(root.glob("**/chats/*.json"))
    # Also session/history style files if present
    chat_files += list(root.glob("tmp/**/*.json"))

    seen: set[Path] = set()
    for path in chat_files:
        if path in seen:
            continue
        seen.add(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        _walk_gemini_obj(data, start, end, by_model, by_day, totals)

    models = [
        ModelUsage(
            model=name,
            input_tokens=int(m["input_tokens"]),
            output_tokens=int(m["output_tokens"]),
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
    return {**totals, "models": models, "daily": daily}


def _walk_gemini_obj(
    obj: Any,
    start: date,
    end: date,
    by_model: dict[str, dict[str, int | float]],
    by_day: dict[date, dict[str, int | float]],
    totals: dict[str, int | float],
) -> None:
    if isinstance(obj, list):
        for item in obj:
            _walk_gemini_obj(item, start, end, by_model, by_day, totals)
        return
    if not isinstance(obj, dict):
        return

    # Common message shapes with usageMetadata
    usage = obj.get("usageMetadata") or obj.get("usage_metadata") or obj.get("usage")
    if isinstance(usage, dict) and (
        "promptTokenCount" in usage
        or "candidatesTokenCount" in usage
        or "totalTokenCount" in usage
        or "input_tokens" in usage
    ):
        day = (
            parse_iso_date(obj.get("timestamp"))
            or parse_iso_date(obj.get("createTime"))
            or parse_iso_date(obj.get("created_at"))
            or parse_iso_date(obj.get("time"))
        )
        # if nested messages have no date, still count if parent walk set day later
        if day is None or (start <= day <= end):
            model = str(
                obj.get("model")
                or obj.get("modelVersion")
                or (obj.get("config") or {}).get("model")
                or "gemini"
            )
            inp = safe_int(
                usage.get("promptTokenCount")
                or usage.get("prompt_token_count")
                or usage.get("input_tokens")
            )
            out = safe_int(
                usage.get("candidatesTokenCount")
                or usage.get("candidates_token_count")
                or usage.get("output_tokens")
            )
            if not out:
                # sometimes only total
                total = safe_int(usage.get("totalTokenCount") or usage.get("total_tokens"))
                if total and not inp:
                    inp = total
            if inp or out:
                cost = estimate_cost(model, inp, out) or 0.0
                m = by_model[model]
                m["input_tokens"] += inp
                m["output_tokens"] += out
                m["requests"] += 1
                m["cost_usd"] += cost
                totals["input_tokens"] += inp
                totals["output_tokens"] += out
                totals["requests"] += 1
                totals["cost_usd"] += cost
                if day and start <= day <= end:
                    d = by_day[day]
                    d["input_tokens"] += inp
                    d["output_tokens"] += out
                    d["requests"] += 1
                    d["cost_usd"] += cost

    for v in obj.values():
        if isinstance(v, (dict, list)):
            _walk_gemini_obj(v, start, end, by_model, by_day, totals)


def _list_models(api_key: str) -> list[str]:
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(url, params={"key": api_key})
        resp.raise_for_status()
        body = resp.json()
    names: list[str] = []
    for m in body.get("models") or []:
        name = m.get("name") or m.get("displayName")
        if name:
            names.append(str(name).removeprefix("models/"))
    return names
