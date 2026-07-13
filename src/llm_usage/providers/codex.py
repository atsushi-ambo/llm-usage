"""OpenAI Codex (ChatGPT free/plus/pro plan) — local rollouts + live quota."""

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
from llm_usage.providers.base import parse_iso_date, safe_int


def collect_codex(settings: Settings, start: date, end: date) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.CODEX,
        display_name="Codex (ChatGPT plan)",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={
            "console_url": "https://chatgpt.com",
            "docs_url": "https://developers.openai.com/codex",
        },
    )

    root = settings.codex_home_dir
    local = _scan_sessions(root / "sessions", start, end)
    if local["requests"] > 0:
        report.source = SourceKind.LOCAL_LOGS
        report.input_tokens = local["input_tokens"]
        report.output_tokens = local["output_tokens"]
        report.cache_read_tokens = local["cache_read_tokens"]
        report.requests = local["requests"]
        report.cost_usd = None  # plan quota, not Platform API $
        report.models = local["models"]
        report.daily = local["daily"]
        report.meta["sessions"] = local["sessions"]
        report.meta["plan_type_seen"] = local.get("plan_type")
        report.notes.append(
            f"From local Codex rollouts under {root / 'sessions'} "
            "(ChatGPT plan usage, not Platform API billing)."
        )
        if local.get("plan_type"):
            report.notes.append(f"Plan type in logs: {local['plan_type']}")

    auth_path = root / "auth.json"
    token_info = _read_codex_auth(auth_path)
    if token_info:
        try:
            live = _fetch_wham_usage(
                token_info["access_token"], token_info.get("account_id")
            )
            report.meta["subscription"] = live
            plan = live.get("plan_type")
            if plan:
                report.meta["plan_type"] = plan
            if report.source == SourceKind.UNAVAILABLE:
                report.source = SourceKind.SUBSCRIPTION
            rate = live.get("rate_limit") or {}
            primary = rate.get("primary_window") or {}
            used = primary.get("used_percent")
            if used is not None:
                report.notes.append(
                    f"Live Codex quota: {used}% of primary window used "
                    f"(plan={plan or 'unknown'}, allowed={rate.get('allowed')})."
                )
            elif plan:
                report.notes.append(f"Live Codex plan: {plan}")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"Codex quota API: {exc}")
    elif not (root / "sessions").exists():
        report.notes.append(
            "No ~/.codex found. Install Codex CLI and sign in with ChatGPT "
            "(works on Free plan)."
        )
    else:
        report.notes.append(
            "Codex sessions found but no usable auth token — re-login with Codex "
            "to enable live quota."
        )

    if report.source == SourceKind.UNAVAILABLE:
        report.notes.append(
            "Codex free/plus usage appears after you use Codex CLI or the IDE extension."
        )

    return report


def _read_codex_auth(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        return None
    access = tokens.get("access_token")
    if not access:
        return None
    out: dict[str, str] = {"access_token": str(access)}
    if tokens.get("account_id"):
        out["account_id"] = str(tokens["account_id"])
    return out


def _fetch_wham_usage(access_token: str, account_id: str | None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "llm-usage/0.1.0",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(
            "https://chatgpt.com/backend-api/wham/usage", headers=headers
        )
        resp.raise_for_status()
        return resp.json()


def _scan_sessions(root: Path, start: date, end: date) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "requests": 0,
        "sessions": 0,
        "models": [],
        "daily": [],
        "plan_type": None,
    }
    if not root.exists():
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
    sessions = 0
    plan_type: str | None = None

    for path in root.rglob("*.jsonl"):
        sessions += 1
        current_model = "codex"
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

                    rtype = row.get("type")
                    payload = (
                        row.get("payload")
                        if isinstance(row.get("payload"), dict)
                        else {}
                    )

                    if rtype == "turn_context":
                        model = payload.get("model")
                        if model:
                            current_model = str(model)
                        continue

                    if rtype != "event_msg" or payload.get("type") != "token_count":
                        continue

                    day = parse_iso_date(row.get("timestamp"))
                    if day is None or day < start or day > end:
                        continue

                    last = (payload.get("info") or {}).get("last_token_usage") or {}
                    inp = safe_int(last.get("input_tokens"))
                    out = safe_int(last.get("output_tokens")) + safe_int(
                        last.get("reasoning_output_tokens")
                    )
                    cache_r = safe_int(last.get("cached_input_tokens"))
                    if not any((inp, out, cache_r)):
                        continue

                    limits = payload.get("rate_limits") or {}
                    if limits.get("plan_type"):
                        plan_type = str(limits["plan_type"])

                    m = by_model[current_model]
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
            continue

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
        "sessions": sessions,
        "models": models,
        "daily": daily,
        "plan_type": plan_type,
    }
