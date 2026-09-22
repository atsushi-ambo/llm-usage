"""Quota burn-rate projection: "at this pace, when do I hit 100%?"

Given used% and (when known) the quota window's start/end, estimate the
linear burn rate and project exhaustion. Pure functions — compute at
*display* time so a cached report still ages correctly as the clock moves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class BurnProjection:
    """Linear projection of quota exhaustion within the current window."""

    used_percent: float
    remaining_percent: float
    pct_per_hour: float | None
    pct_per_day: float | None
    hours_to_exhaustion: float | None
    hits_at: datetime | None
    hits_before_reset: bool | None
    resets_at: datetime | None
    # Short menubar/CLI chip: "hits Fri", "hits in 2h", "ok till reset", "exhausted"
    hits_label: str
    # Longer UI sentence for dashboard / notes
    summary: str
    confidence: str  # "high" | "medium" | "low"


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # Date-only (Grok period end sometimes)
            try:
                dt = datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def infer_window_seconds(
    *,
    label: str | None = None,
    period_start: Any = None,
    resets_at: Any = None,
    window_seconds: float | int | None = None,
    window_key: str | None = None,
) -> float | None:
    """Best-effort quota window length in seconds."""
    if isinstance(window_seconds, (int, float)) and window_seconds > 0:
        return float(window_seconds)

    start = _parse_dt(period_start)
    end = _parse_dt(resets_at)
    if start is not None and end is not None and end > start:
        return (end - start).total_seconds()

    key = (window_key or "").lower()
    if key in ("five_hour", "5_hour", "5h"):
        return 5 * 3600.0
    if key in ("seven_day", "7_day", "weekly"):
        return 7 * 86400.0
    if key in ("seven_day_sonnet", "seven_day_opus"):
        return 7 * 86400.0

    text = label or ""
    m = re.search(r"(\d+)\s*-\s*day|(\d+)\s*day", text, re.I)
    if m:
        days = int(m.group(1) or m.group(2))
        if days > 0:
            return float(days * 86400)
    m = re.search(r"(\d+)\s*-\s*hour|(\d+)\s*h\b", text, re.I)
    if m:
        hours = int(m.group(1) or m.group(2))
        if hours > 0:
            return float(hours * 3600)
    if re.search(r"weekly", text, re.I):
        return 7 * 86400.0
    if re.search(r"5[-\s]?hour", text, re.I):
        return 5 * 3600.0
    if re.search(r"7[-\s]?day|seven[-\s]?day", text, re.I):
        return 7 * 86400.0
    return None


def _format_hits_at_portable(hits_at: datetime, now: datetime) -> str:
    """Compact absolute/relative label for menubar/CLI chips."""
    delta = hits_at - now
    secs = delta.total_seconds()
    if secs <= 0:
        return "exhausted"
    if secs < 3600:
        mins = max(1, int(round(secs / 60)))
        return f"hits in {mins}m"
    if secs < 36 * 3600:
        hours = max(1, int(round(secs / 3600)))
        return f"hits in {hours}h"
    if hits_at.date() <= (now + timedelta(days=6)).date():
        return f"hits {hits_at.strftime('%A')}"
    # Portable day-of-month without leading zero gymnastics
    return f"hits {hits_at.strftime('%b')} {hits_at.day}"


def project_burn(
    used_percent: float | int | None,
    *,
    resets_at: Any = None,
    period_start: Any = None,
    window_label: str | None = None,
    window_seconds: float | int | None = None,
    window_key: str | None = None,
    now: datetime | None = None,
) -> BurnProjection | None:
    """Project when used_percent reaches 100% at the current linear rate.

    Requires enough signal to know how long the window has been open
    (period_start, window_seconds + resets_at, or a parseable label + resets_at).
    Returns None when used% is missing.
    """
    if used_percent is None:
        return None
    try:
        used = max(0.0, min(100.0, float(used_percent)))
    except (TypeError, ValueError):
        return None

    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    else:
        now_dt = now_dt.astimezone(timezone.utc)

    remaining = 100.0 - used
    reset_dt = _parse_dt(resets_at)

    if used >= 100.0:
        return BurnProjection(
            used_percent=used,
            remaining_percent=0.0,
            pct_per_hour=None,
            pct_per_day=None,
            hours_to_exhaustion=0.0,
            hits_at=now_dt,
            hits_before_reset=True,
            resets_at=reset_dt,
            hits_label="exhausted",
            summary="Quota exhausted",
            confidence="high",
        )

    if used <= 0.0:
        label = "idle"
        summary = "No usage yet in this window"
        if reset_dt is not None:
            summary += f" · resets {_human_reset(reset_dt, now_dt)}"
        return BurnProjection(
            used_percent=0.0,
            remaining_percent=100.0,
            pct_per_hour=0.0,
            pct_per_day=0.0,
            hours_to_exhaustion=None,
            hits_at=None,
            hits_before_reset=False,
            resets_at=reset_dt,
            hits_label=label,
            summary=summary,
            confidence="high",
        )

    win_secs = infer_window_seconds(
        label=window_label,
        period_start=period_start,
        resets_at=resets_at,
        window_seconds=window_seconds,
        window_key=window_key,
    )
    start_dt = _parse_dt(period_start)
    confidence = "low"
    if start_dt is not None:
        confidence = "high"
    elif win_secs is not None and reset_dt is not None:
        start_dt = reset_dt - timedelta(seconds=win_secs)
        confidence = "medium" if window_seconds or period_start else "medium"
    elif win_secs is not None:
        # No reset known — assume window started win_secs ago (weak)
        start_dt = now_dt - timedelta(seconds=win_secs)
        confidence = "low"
    else:
        # Cannot estimate rate without elapsed time
        summary = f"{remaining:.0f}% remaining"
        if reset_dt is not None:
            summary += f" · resets {_human_reset(reset_dt, now_dt)}"
        return BurnProjection(
            used_percent=used,
            remaining_percent=remaining,
            pct_per_hour=None,
            pct_per_day=None,
            hours_to_exhaustion=None,
            hits_at=None,
            hits_before_reset=None,
            resets_at=reset_dt,
            hits_label=f"{remaining:.0f}% left",
            summary=summary,
            confidence="low",
        )

    elapsed = (now_dt - start_dt).total_seconds()
    # Floor elapsed so a just-opened window with a spike doesn't explode
    # the rate; still allow short 5-hour windows to project.
    elapsed = max(elapsed, 60.0)
    if win_secs is not None:
        # Don't credit more elapsed than the window length (clock skew)
        elapsed = min(elapsed, float(win_secs))

    elapsed_hours = elapsed / 3600.0
    pct_per_hour = used / elapsed_hours
    pct_per_day = pct_per_hour * 24.0

    if pct_per_hour <= 1e-9:
        return BurnProjection(
            used_percent=used,
            remaining_percent=remaining,
            pct_per_hour=0.0,
            pct_per_day=0.0,
            hours_to_exhaustion=None,
            hits_at=None,
            hits_before_reset=False,
            resets_at=reset_dt,
            hits_label="idle",
            summary=f"{used:.0f}% used · burn rate ~0",
            confidence=confidence,
        )

    hours_to_100 = remaining / pct_per_hour
    hits_at = now_dt + timedelta(hours=hours_to_100)

    hits_before_reset: bool | None
    if reset_dt is not None:
        hits_before_reset = hits_at < reset_dt
    else:
        hits_before_reset = None

    if hits_before_reset is False and reset_dt is not None:
        hits_label = "ok till reset"
        summary = (
            f"At current pace ({pct_per_day:.0f}%/day) you stay under 100% "
            f"until reset {_human_reset(reset_dt, now_dt)}"
        )
    else:
        hits_label = _format_hits_at_portable(hits_at, now_dt)
        summary = (
            f"At current pace ({pct_per_day:.0f}%/day) hits 100% "
            f"{_human_reset(hits_at, now_dt)}"
        )
        if reset_dt is not None:
            summary += f" (before reset {_human_reset(reset_dt, now_dt)})"

    return BurnProjection(
        used_percent=used,
        remaining_percent=remaining,
        pct_per_hour=pct_per_hour,
        pct_per_day=pct_per_day,
        hours_to_exhaustion=hours_to_100,
        hits_at=hits_at,
        hits_before_reset=hits_before_reset,
        resets_at=reset_dt,
        hits_label=hits_label,
        summary=summary,
        confidence=confidence,
    )


def _human_reset(dt: datetime, now: datetime) -> str:
    local = dt.astimezone()
    now_local = now.astimezone()
    if local.date() == now_local.date():
        return f"today {local.strftime('%H:%M')}"
    if local.date() == (now_local + timedelta(days=1)).date():
        return f"tomorrow {local.strftime('%H:%M')}"
    if local.date() <= (now_local + timedelta(days=6)).date():
        return local.strftime("%a %H:%M")
    return f"{local.strftime('%b')} {local.day}, {local.strftime('%H:%M')}"


def projection_to_dict(proj: BurnProjection) -> dict[str, Any]:
    """JSON-friendly form for API / dashboard."""
    return {
        "used_percent": proj.used_percent,
        "remaining_percent": proj.remaining_percent,
        "pct_per_hour": proj.pct_per_hour,
        "pct_per_day": proj.pct_per_day,
        "hours_to_exhaustion": proj.hours_to_exhaustion,
        "hits_at": proj.hits_at.isoformat() if proj.hits_at else None,
        "hits_before_reset": proj.hits_before_reset,
        "resets_at": proj.resets_at.isoformat() if proj.resets_at else None,
        "hits_label": proj.hits_label,
        "summary": proj.summary,
        "confidence": proj.confidence,
    }


def project_from_quota(
    quota: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> BurnProjection | None:
    """Convenience: project from a provider meta['quota'] dict."""
    if not isinstance(quota, dict):
        return None
    used = quota.get("used_percent")
    if used is None:
        return None
    return project_burn(
        used,
        resets_at=quota.get("resets_at"),
        period_start=quota.get("period_start"),
        window_label=quota.get("label"),
        window_seconds=quota.get("window_seconds") or quota.get("limit_window_seconds"),
        window_key=quota.get("key"),
        now=now,
    )


def enrich_quota_dict(
    quota: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a copy of quota with `burn` (and per-window burn) attached."""
    out = dict(quota)
    primary = project_from_quota(out, now=now)
    if primary is not None:
        out["burn"] = projection_to_dict(primary)
    windows = out.get("windows")
    if isinstance(windows, list):
        enriched_windows: list[Any] = []
        for w in windows:
            if not isinstance(w, dict):
                enriched_windows.append(w)
                continue
            wc = dict(w)
            # Windows inherit period/window length from parent when missing
            wp = project_burn(
                wc.get("used_percent"),
                resets_at=wc.get("resets_at") or out.get("resets_at"),
                period_start=wc.get("period_start") or out.get("period_start"),
                window_label=wc.get("label") or out.get("label"),
                window_seconds=wc.get("window_seconds")
                or out.get("window_seconds")
                or out.get("limit_window_seconds"),
                window_key=wc.get("key"),
                now=now,
            )
            if wp is not None:
                wc["burn"] = projection_to_dict(wp)
            enriched_windows.append(wc)
        out["windows"] = enriched_windows
    return out
