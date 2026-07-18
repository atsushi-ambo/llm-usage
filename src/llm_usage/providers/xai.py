"""Grok / xAI — live weekly credits + Grok Build local logs."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from llm_usage.config import Settings
from llm_usage.logcache import scan_with_cache
from llm_usage.models import (
    DailyPoint,
    ModelUsage,
    ProviderId,
    ProviderReport,
    SourceKind,
)
from llm_usage.providers.base import parse_iso_date, safe_error_str, safe_float, safe_int

_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def collect_xai(
    settings: Settings,
    start: date,
    end: date,
    *,
    quota_only: bool = False,
) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.GROK,
        display_name="Grok Build / xAI",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={
            "console_url": "https://grok.com",
            "billing_url": "https://grok.com",  # Settings → 使用量
            "grok_build": "https://x.ai/news/grok-build-cli",
        },
    )

    # 1) Live weekly credits (same source as Grok settings) — never use stale logs for %
    live_billing: dict[str, Any] | None = None
    try:
        live_billing = _fetch_live_credits(settings.grok_home_dir)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"Live Grok billing: {exc}")

    # 2) Local inference logs for token totals (skipped in quota_only / menubar)
    build: dict[str, Any] = {}
    if not quota_only:
        build = _scan_grok_build(settings.grok_home_dir, start, end)
        if build["requests"] > 0:
            report.source = SourceKind.LOCAL_LOGS
            report.input_tokens = build["input_tokens"]
            report.output_tokens = build["output_tokens"]
            report.cache_read_tokens = build["cache_read_tokens"]
            report.requests = build["requests"]
            report.models = build["models"]
            report.daily = build["daily"]
            report.meta["sessions"] = build.get("sessions")
            report.cost_usd = None
            report.notes.append(
                "Token totals from ~/.grok/logs/unified.jsonl "
                "(inference_done events in range)."
            )

    # Prefer live billing for quota %; fall back to log only if period still active
    billing = live_billing
    billing_source = "live"
    if billing is None and not quota_only:
        log_billing = build.get("billing")
        if log_billing and not _period_ended(log_billing.get("period") or {}):
            billing = log_billing
            billing_source = "local_logs"
        elif log_billing and _period_ended(log_billing.get("period") or {}):
            report.notes.append(
                "Local Grok billing snapshot is from a past weekly period "
                f"(ended {(log_billing.get('period') or {}).get('end', '?')[:10]}); "
                "quota % hidden until live fetch works — open Grok Build once or "
                "run `grok login`."
            )
            report.meta["stale_billing"] = log_billing

    if billing:
        report.meta["subscription"] = billing
        report.meta["billing_source"] = billing_source
        _apply_billing_quota(report, billing, source=billing_source)
        if report.source == SourceKind.UNAVAILABLE:
            report.source = SourceKind.API if billing_source == "live" else SourceKind.LOCAL_LOGS

    if quota_only:
        # Menubar only needs the credit bar — skip session walk + model lists.
        if report.source == SourceKind.UNAVAILABLE:
            report.notes.append(
                "No Grok Build data found. Run `grok` (X Premium / SuperGrok) or set "
                "XAI_API_KEY for the developer API."
            )
        return report

    # 3) Session summaries as backup activity counts
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

    # 4) Optional platform API key (pay-as-you-go, separate from X Premium)
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
            report.errors.append(f"xAI API: {safe_error_str(exc)}")

    if settings.xai_management_key and settings.xai_team_id:
        try:
            info = _list_team_keys(settings.xai_management_key, settings.xai_team_id)
            report.meta["api_keys"] = info
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.API
            report.notes.append("Management API connected (API keys listed).")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Management API: {safe_error_str(exc)}")

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "No Grok Build data found. Run `grok` (X Premium / SuperGrok) or set "
            "XAI_API_KEY for the developer API."
        )

    return report


def _period_ended(period: dict[str, Any]) -> bool:
    end_raw = period.get("end")
    if not end_raw:
        return False
    try:
        end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= end_dt
    except ValueError:
        return False


def _apply_billing_quota(
    report: ProviderReport, billing: dict[str, Any], *, source: str
) -> None:
    tier = billing.get("subscription_tier") or "X Premium"
    pct = billing.get("credit_usage_percent")
    period = billing.get("period") or {}
    products = billing.get("product_usage") or []

    windows: list[dict[str, Any]] = []
    for prod in products:
        if not isinstance(prod, dict):
            continue
        name = str(prod.get("product") or prod.get("name") or "product")
        up = safe_float(prod.get("usagePercent") or prod.get("usage_percent"))
        if up is None:
            continue
        windows.append(
            {
                "key": name.lower(),
                "label": name,
                "used_percent": max(0.0, min(100.0, float(up))),
            }
        )

    if pct is not None:
        report.meta["quota"] = {
            "used_percent": float(pct),
            "label": "Weekly limit",
            "plan": tier,
            "resets_at": period.get("end"),
            "period_start": period.get("start"),
            "period_type": period.get("type") or "weekly",
            "windows": windows,
            "source": source,
        }
    note = f"Grok Build · plan={tier}"
    if pct is not None:
        note += f" · weekly quota ~{pct:.0f}% used"
    if period.get("start") and period.get("end"):
        note += f" ({str(period['start'])[:10]} → {str(period['end'])[:10]})"
    if source == "live":
        note += " · live"
    report.notes.insert(0, note)


def _read_grok_auth_token(home: Path) -> str | None:
    path = home / "auth.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # Prefer newest-looking entry with a key
    for _k, entry in data.items():
        if isinstance(entry, dict):
            tok = entry.get("key") or entry.get("access_token") or entry.get("accessToken")
            if tok:
                return str(tok)
    return None


def _fetch_live_credits(home: Path) -> dict[str, Any] | None:
    """Live weekly credits from cli-chat-proxy (same as Grok Build / settings)."""
    token = _read_grok_auth_token(home)
    if not token:
        raise RuntimeError("No Grok auth token in ~/.grok/auth.json — run `grok login`")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "llm-usage/0.1.0",
        "x-grok-client-version": "0.2.99",
        "x-grok-client-mode": "cli",
    }
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(_BILLING_URL, headers=headers)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Grok auth rejected ({resp.status_code}) — run `grok login` again"
            )
        resp.raise_for_status()
        body = resp.json()

    cfg = body.get("config") if isinstance(body, dict) else None
    if not isinstance(cfg, dict):
        raise RuntimeError("Unexpected billing response shape")

    period = cfg.get("currentPeriod") or {}
    products = cfg.get("productUsage") or cfg.get("product_usage") or []
    return {
        "subscription_tier": body.get("subscriptionTier")
        or cfg.get("subscriptionTier")
        or "X Premium",
        "credit_usage_percent": safe_float(cfg.get("creditUsagePercent")),
        "period": {
            "type": period.get("type"),
            "start": period.get("start"),
            "end": period.get("end"),
        },
        "on_demand_used": (cfg.get("onDemandUsed") or {}).get("val"),
        "on_demand_cap": (cfg.get("onDemandCap") or {}).get("val"),
        "prepaid_balance": (cfg.get("prepaidBalance") or {}).get("val"),
        "product_usage": products if isinstance(products, list) else [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "live": True,
    }


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

    # sid -> model resolution happens here (not inside the cached parse)
    # since session_model is loaded fresh from separate summary.json files
    # every call; baking the model into the cache would let it go stale if
    # a summary is written/updated after its inference was logged.
    parsed = scan_with_cache("grok", log_path, _parse_grok_build_file)
    latest_billing = parsed.get("billing")

    for rec in parsed.get("records", []):
        day = date.fromisoformat(rec["day"])
        if day < start or day > end:
            continue
        inp, out, cache_r = rec["input_tokens"], rec["output_tokens"], rec["cache_read_tokens"]

        sid = rec["sid"]
        if sid:
            session_ids.add(sid)
        # Don't guess a specific version when we can't map the session to a
        # model — it would misattribute tokens/cost to whatever "grok-4.5"
        # happens to price at.
        model = session_model.get(sid) or "grok (unknown)"

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


def _parse_grok_build_file(path: Path) -> dict[str, Any]:
    """Whole-file parse (no date filtering) so the cached result stays
    valid across different --days windows; see llm_usage.logcache.

    Model attribution (sid -> model) deliberately isn't resolved here —
    see the caller, which does that from freshly-loaded session summaries
    on every call instead of baking it into this file-level cache.
    """
    records: list[dict[str, Any]] = []
    latest_billing: dict[str, Any] | None = None
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

                msg = row.get("msg") or ""

                if msg == "billing: fetched credits config":
                    # Fallback only — live API is preferred (logs go stale after weekly reset)
                    ctx = row.get("ctx") or {}
                    cfg = ctx.get("config") or {}
                    period = cfg.get("currentPeriod") or {}
                    products = cfg.get("productUsage") or []
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
                        "product_usage": products if isinstance(products, list) else [],
                        "fetched_at": row.get("ts"),
                        "live": False,
                    }
                    continue

                if msg != "shell.turn.inference_done":
                    continue
                day = parse_iso_date(row.get("ts"))
                if day is None:
                    continue

                ctx = row.get("ctx") or {}
                inp = safe_int(ctx.get("prompt_tokens"))
                cache_r = safe_int(ctx.get("cached_prompt_tokens"))
                # completion_tokens is the main output counter; reasoning is often
                # a subset of the same stream, so don't add them together.
                out = safe_int(ctx.get("completion_tokens"))
                if not any((inp, out, cache_r)):
                    continue

                records.append(
                    {
                        "day": day.isoformat(),
                        "sid": str(row.get("sid") or ""),
                        "input_tokens": inp,
                        "output_tokens": out,
                        "cache_read_tokens": cache_r,
                    }
                )
    except OSError:
        pass
    return {"records": records, "billing": latest_billing}


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
