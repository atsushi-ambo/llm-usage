"""Helpers to normalize and cache subscription quota snapshots."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def cache_dir() -> Path:
    d = Path.home() / ".config" / "llm-usage" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json_cache(name: str, max_age_s: float = 3600) -> dict[str, Any] | None:
    path = cache_dir() / name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    ts = data.get("_cached_at")
    if not isinstance(ts, (int, float)):
        return None
    if time.time() - ts > max_age_s:
        return None
    body = data.get("payload")
    return body if isinstance(body, dict) else None


def write_json_cache(name: str, payload: dict[str, Any]) -> None:
    path = cache_dir() / name
    try:
        path.write_text(
            json.dumps({"_cached_at": time.time(), "payload": payload}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def read_json_cache_stale(name: str) -> dict[str, Any] | None:
    """Return cached payload even if expired (for 429 fallback)."""
    path = cache_dir() / name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    body = data.get("payload")
    return body if isinstance(body, dict) else None


def unix_to_iso(ts: Any) -> str | None:
    if isinstance(ts, (int, float)) and ts > 0:
        # ms vs s
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    if isinstance(ts, str) and ts:
        return ts
    return None


def claude_quota_from_oauth(data: dict[str, Any], *, plan: str | None = None) -> dict[str, Any]:
    """Build normalized quota from Anthropic GET /api/oauth/usage body."""
    windows: list[dict[str, Any]] = []

    def _win(key: str, label: str) -> None:
        block = data.get(key)
        if not isinstance(block, dict):
            return
        # utilization is 0–1 or 0–100 depending on version
        util = block.get("utilization")
        if util is None:
            util = block.get("utilization_pct")
        pct: float | None = None
        if isinstance(util, (int, float)):
            pct = float(util) * 100.0 if float(util) <= 1.0 else float(util)
        elif block.get("used") is not None and block.get("limit"):
            try:
                pct = 100.0 * float(block["used"]) / float(block["limit"])
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        if pct is None:
            return
        windows.append(
            {
                "key": key,
                "label": label,
                "used_percent": max(0.0, min(100.0, pct)),
                "used": block.get("used"),
                "limit": block.get("limit"),
                "resets_at": unix_to_iso(block.get("resets_at") or block.get("resetsAt")),
            }
        )

    _win("five_hour", "5-hour")
    _win("seven_day", "7-day")
    _win("seven_day_sonnet", "7-day Sonnet")
    _win("seven_day_opus", "7-day Opus")

    # Primary bar: prefer 7-day overall, else first window
    primary = next((w for w in windows if w["key"] == "seven_day"), None)
    if primary is None and windows:
        primary = windows[0]

    if primary is None:
        return {
            "used_percent": None,
            "label": "Claude Code",
            "plan": plan,
            "resets_at": None,
            "windows": [],
        }

    return {
        "used_percent": primary["used_percent"],
        "label": primary["label"] + " limit",
        "plan": plan or "Claude",
        "resets_at": primary.get("resets_at"),
        "windows": windows,
    }
