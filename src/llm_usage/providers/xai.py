"""Grok / xAI — Grok Build (X Premium) local logs + optional API keys."""

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
from llm_usage.providers.base import parse_iso_date, safe_float, safe_int


def collect_xai(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.GROK,
        display_name="Grok Build / xAI",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={
            "console_url": "https://console.x.ai/team/default/usage",
            "billing_url": "https://console.x.ai/team/default/billing",
            "grok_build": "https://x.ai/news/grok-build-cli",
        },
    )

    # 1) Grok Build local inference logs (best source for X Premium users)
    build = _scan_grok_build(settings.grok_home_dir, start, end)
    if build["requests"] > 0 or build.get("billing"):
        report.source = SourceKind.LOCAL_LOGS
        report.input_tokens = build["input_tokens"]
        report.output_tokens = build["output_tokens"]
        report.cache_read_tokens = build["cache_read_tokens"]
        report.requests = build["requests"]
        report.models = build["models"]
        report.daily = build["daily"]
        report.meta["sessions"] = build.get("sessions")
        report.meta["subscription"] = build.get("billing")
        # Subscription-included: no $ invoice for normal X Premium usage
        report.cost_usd = None
        if build.get("billing"):
            b = build["billing"]
            tier = b.get("subscription_tier") or "X Premium"
            pct = b.get("credit_usage_percent")
            period = b.get("period") or {}
            # Normalized quota for dashboard progress bars
            if pct is not None:
                report.meta["quota"] = {
                    "used_percent": float(pct),
                    "label": "Weekly limit",
                    "plan": tier,
                    "resets_at": period.get("end"),
                    "period_start": period.get("start"),
                    "period_type": period.get("type") or "weekly",
                }
            note = f"Grok Build · plan={tier}"
            if pct is not None:
                note += f" · weekly quota ~{pct:.0f}% used"
            if period.get("start") and period.get("end"):
                note += f" ({period['start'][:10]} → {period['end'][:10]})"
            report.notes.append(note)
        report.notes.append(
            f"Token totals from ~/.grok/logs/unified.jsonl "
            f"(inference_done events in range)."
        )

    # 2) Session summaries as backup activity counts
    sess = _scan_session_summaries(settings.grok_home_dir / "sessions", start, end)
    if sess["session_count"]:
        report.meta["session_summaries"] = sess
        if report.source == SourceKind.UNAVAILABLE:
            report.source = SourceKind.LOCAL_LOGS
            report.requests = sess["messages"]
            report.models = [
                ModelUsage(model=m, requests=c) for m, c in sess["models"].items()
            ]
            report.notes.append(
                f"{sess['session_count']} Grok Build session(s); "
                "open sessions for activity (no per-token log yet in range)."
            )

    # 3) Optional platform API key (pay-as-you-go, separate from X Premium)
    if settings.xai_api_key:
        try:
            models = _list_models(settings.xai_api_key)
            report.meta["available_models"] = models
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.API
            report.notes.append(
                f"XAI_API_KEY valid — {len(models)} model(s). "
                "API pay-as-you-go spend is in console.x.ai (separate from Grok Build)."
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"xAI API: {exc}")

    if settings.xai_management_key and settings.xai_team_id:
        try:
            info = _list_team_keys(settings.xai_management_key, settings.xai_team_id)
            report.meta["api_keys"] = info
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.API
            report.notes.append("Management API connected (API keys listed).")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Management API: {exc}")

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "No Grok Build data found. Run `grok` (X Premium / SuperGrok) or set "
            "XAI_API_KEY for the developer API."
        )

    return report


def _scan_grok_build(home: Path, start: date, end: date) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "requests": 0,
        "models": [],
        "daily": [],
        "sessions": 0,
        "billing": None,
    }
    log_path = home / "logs" / "unified.jsonl"
    if not log_path.exists():
        return empty

    by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "requests": 0,
        }
    )
    by_day: dict[date, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    )
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "requests": 0,
    }
    session_ids: set[str] = set()
    # Map session → model from concurrent session summaries when possible
    session_model: dict[str, str] = {}
    for summary in (home / "sessions").rglob("summary.json") if (home / "sessions").exists() else []:
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            sid = (data.get("info") or {}).get("id") or summary.parent.name
            model = data.get("current_model_id")
            if sid and model:
                session_model[str(sid)] = str(model)
        except (OSError, json.JSONDecodeError):
            continue

    latest_billing: dict[str, Any] | None = None

    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = row.get("msg") or ""
                day = parse_iso_date(row.get("ts"))

                if msg == "billing: fetched credits config":
                    ctx = row.get("ctx") or {}
                    cfg = ctx.get("config") or {}
                    period = cfg.get("currentPeriod") or {}
                    latest_billing = {
                        "subscription_tier": ctx.get("subscriptionTier"),
                        "credit_usage_percent": safe_float(cfg.get("creditUsagePercent")),
                        "period": {
                            "type": period.get("type"),
                            "start": period.get("start"),
                            "end": period.get("end"),
                        },
                        "on_demand_used": (cfg.get("onDemandUsed") or {}).get("val"),
                        "on_demand_cap": (cfg.get("onDemandCap") or {}).get("val"),
                        "prepaid_balance": (cfg.get("prepaidBalance") or {}).get("val"),
                        "fetched_at": row.get("ts"),
                    }
                    continue

                if msg != "shell.turn.inference_done":
                    continue
                if day is None or day < start or day > end:
                    continue

                ctx = row.get("ctx") or {}
                inp = safe_int(ctx.get("prompt_tokens"))
                cache_r = safe_int(ctx.get("cached_prompt_tokens"))
                # completion_tokens is the main output counter; reasoning is often
                # a subset of the same stream, so don't add them together.
                out = safe_int(ctx.get("completion_tokens"))
                if not any((inp, out, cache_r)):
                    continue

                sid = str(row.get("sid") or "")
                if sid:
                    session_ids.add(sid)
                model = session_model.get(sid) or "grok-4.5"

                m = by_model[model]
                m["input_tokens"] += inp
                m["output_tokens"] += out
                m["cache_read_tokens"] += cache_r
                m["requests"] += 1

                d = by_day[day]
                d["input_tokens"] += inp
                d["output_tokens"] += out
                d["requests"] += 1

                totals["input_tokens"] += inp
                totals["output_tokens"] += out
                totals["cache_read_tokens"] += cache_r
                totals["requests"] += 1
    except OSError:
        return empty

    models = [
        ModelUsage(
            model=name,
            input_tokens=int(m["input_tokens"]),
            output_tokens=int(m["output_tokens"]),
            cache_read_tokens=int(m["cache_read_tokens"]),
            requests=int(m["requests"]),
            cost_usd=None,
        )
        for name, m in sorted(by_model.items(), key=lambda x: -x[1]["requests"])
    ]
    daily = [
        DailyPoint(
            day=d,
            input_tokens=int(v["input_tokens"]),
            output_tokens=int(v["output_tokens"]),
            requests=int(v["requests"]),
            cost_usd=None,
        )
        for d, v in sorted(by_day.items())
    ]
    return {
        **totals,
        "models": models,
        "daily": daily,
        "sessions": len(session_ids),
        "billing": latest_billing,
    }


def _scan_session_summaries(root: Path, start: date, end: date) -> dict[str, Any]:
    if not root.exists():
        return {"session_count": 0, "messages": 0, "models": {}}
    count = 0
    messages = 0
    models: dict[str, int] = defaultdict(int)
    for path in root.rglob("summary.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        day = parse_iso_date(data.get("updated_at") or data.get("created_at") or data.get("last_active_at"))
        if day is None or day < start or day > end:
            continue
        count += 1
        messages += safe_int(data.get("num_chat_messages") or data.get("num_messages"))
        model = data.get("current_model_id") or "grok"
        models[str(model)] += 1
    return {"session_count": count, "messages": messages, "models": dict(models)}


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
