"""Helpers to normalize and cache subscription quota snapshots."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_usage.models import ProviderReport


def cache_dir() -> Path:
    d = Path.home() / ".config" / "llm-usage" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    # The parent config dir may also hold .env with API keys — keep both private.
    for p in (d, d.parent):
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    return d


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
    *,
    indent: int | None = 2,
) -> None:
    """Write JSON with 0600 perms from creation — no window where the file
    briefly exists at the process' default umask before we chmod it.
    `tempfile.mkstemp` creates its file with mode 0600 already; we just
    write into it and rename it into place.

    indent=2 (default) for small human-readable quota snapshots;
    indent=None for compact logscan payloads (roughly half the bytes).
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if indent is None:
                json.dump(data, fh, separators=(",", ":"))
            else:
                json.dump(data, fh, indent=indent)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_cache_file(name: str) -> dict[str, Any] | None:
    path = cache_dir() / name
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_json_cache(name: str, max_age_s: float = 3600) -> dict[str, Any] | None:
    data = _read_cache_file(name)
    if data is None:
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
        atomic_write_json(path, {"_cached_at": time.time(), "payload": payload})
    except OSError:
        pass


def read_json_cache_stale(name: str) -> dict[str, Any] | None:
    """Return cached payload even if expired (for 429 fallback)."""
    data = _read_cache_file(name)
    if data is None:
        return None
    body = data.get("payload")
    return body if isinstance(body, dict) else None


_DASHBOARD_SESSION_FILE = "dashboard_session.json"


def write_dashboard_session(token: str, host: str, port: int) -> None:
    """Record the running dashboard's auth token so the menubar (same user,
    same trust boundary as the CLI) can open an authenticated browser tab."""
    write_json_cache(_DASHBOARD_SESSION_FILE, {"token": token, "host": host, "port": port})


def read_dashboard_session() -> dict[str, Any] | None:
    return read_json_cache_stale(_DASHBOARD_SESSION_FILE)


def cooldown_remaining(name: str) -> float:
    """Seconds left on a rate-limit cooldown marker (0 if none)."""
    data = _read_cache_file(f"{name}.cooldown")
    if data is None:
        return 0.0
    until = data.get("until")
    if not isinstance(until, (int, float)):
        return 0.0
    return max(0.0, until - time.time())


def write_cooldown(name: str, seconds: float) -> None:
    """Remember that an endpoint is rate limited; skip requests until it expires."""
    path = cache_dir() / f"{name}.cooldown"
    try:
        atomic_write_json(path, {"until": time.time() + max(0.0, seconds)})
    except OSError:
        pass


def clear_cooldown(name: str) -> None:
    path = cache_dir() / f"{name}.cooldown"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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

    # Primary bar: prefer 5-hour — it resets soonest and is what blocks you
    # mid-session. 7-day and other windows stay available in `windows`.
    primary = next((w for w in windows if w["key"] == "five_hour"), None)
    if primary is None:
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


def quota_windows(p: "ProviderReport") -> list[tuple[str, float]]:
    """(label, used_percent) for every distinct quota window a provider
    reports. Claude's `windows` list already includes an entry equivalent
    to its top-level quota, so only fall back to the top-level figure for
    providers (Codex, Grok) that don't expose a `windows` breakdown."""
    q = (p.meta or {}).get("quota") or {}
    windows = q.get("windows") or []
    if windows:
        return [
            (w.get("label") or w.get("key") or "window", float(w["used_percent"]))
            for w in windows
            if w.get("used_percent") is not None
        ]
    if q.get("used_percent") is not None:
        return [(q.get("label") or "quota", float(q["used_percent"]))]
    return []
