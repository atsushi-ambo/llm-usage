"""AppKit-free logic for the macOS menu bar.

`menubar.py` has to stay outside pyright's reach: its AppKit/pyobjc symbols
only resolve on macOS with stubs installed, so type checking it in CI would
be noise. That exclusion used to swallow the whole 900-line module —
including the quota-selection and color math, which is ordinary Python with
real edge cases and is where bugs actually live.

Everything here is pure (no AppKit, no rumps, no network): given a report
and an appearance flag, it decides *what* to draw. `menubar.py` keeps only
the thin glue that decides *how* to draw it — NSColor, NSImage, timers,
threads. That seam is what lets this half be type-checked and unit-tested
on any platform.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypedDict

from llm_usage.burnrate import BurnProjection, project_from_quota
from llm_usage.models import AggregateReport, ProviderId, ProviderReport
from llm_usage.quota import atomic_write_json, quota_windows

RGB = tuple[int, int, int]

PREFS_PATH = Path.home() / ".config" / "llm-usage" / "menubar.json"
NOTIFY_THRESHOLDS = (70, 90)

# Default: Grok in the menu bar (user can switch)
DEFAULT_FOCUS = "grok"

# Dashboard jewel tones, darkened so 13pt type and bars read on a light
# NSMenu. Dark mode brightens ~22%. Same hues as the web UI.
PROVIDER_STYLE: dict[str, dict] = {
    "claude": {"letter": "C", "short": "Claude", "rgb": (198, 118, 82)},
    "codex": {"letter": "O", "short": "Codex", "rgb": (42, 148, 98)},
    "openai": {"letter": "O", "short": "OpenAI", "rgb": (32, 140, 112)},
    "grok": {"letter": "G", "short": "Grok", "rgb": (108, 96, 196)},
    "cursor": {"letter": "Cu", "short": "Cursor", "rgb": (56, 122, 210)},
    "gemini": {"letter": "Ge", "short": "Gemini", "rgb": (184, 140, 36)},
    "openrouter": {"letter": "Or", "short": "OpenRouter", "rgb": (28, 148, 140)},
    "cohere": {"letter": "Co", "short": "Cohere", "rgb": (40, 122, 186)},
    "mistral": {"letter": "Mi", "short": "Mistral", "rgb": (196, 90, 78)},
    "replicate": {"letter": "Re", "short": "Replicate", "rgb": (108, 100, 186)},
    "huggingface": {"letter": "Hf", "short": "HuggingFace", "rgb": (176, 148, 36)},
}

FOCUS_ORDER = [
    "grok",
    "codex",
    "claude",
    "cursor",
    "gemini",
    "openrouter",
    "openai",
    "cohere",
    "mistral",
    "replicate",
    "huggingface",
]

_RGB_OK: RGB = (42, 148, 98)
_RGB_WARN: RGB = (184, 140, 36)
_RGB_HOT: RGB = (196, 90, 78)
_RGB_CRIT: RGB = (196, 48, 52)
_RGB_EMPTY_LIGHT: RGB = (214, 216, 220)
_RGB_EMPTY_DARK: RGB = (70, 72, 78)


class Palette(TypedDict):
    """Colors resolved for the menu's current light/dark appearance."""

    dark: bool
    brands: dict[str, RGB]
    ok: RGB
    warn: RGB
    hot: RGB
    crit: RGB
    empty: RGB


# ── preferences ───────────────────────────────────────────────────────


def load_prefs() -> dict:
    if PREFS_PATH.exists():
        try:
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"focus": DEFAULT_FOCUS}


def save_prefs(prefs: dict) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(PREFS_PATH, prefs)
    except OSError:
        pass


# ── quota selection ───────────────────────────────────────────────────


def quota_crossings(
    report: AggregateReport, notified: dict[tuple[str, str], int]
) -> list[tuple[str, str, float, int]]:
    """(display_name, window_label, pct, threshold) for every quota window
    that just crossed a new NOTIFY_THRESHOLDS level, updating `notified` in
    place so the same crossing isn't returned again until the window drops
    back below the lowest threshold (e.g. it reset)."""
    crossings: list[tuple[str, str, float, int]] = []
    for p in report.providers:
        for label, pct in quota_windows(p):
            key = (p.provider.value, label)
            crossed = max((t for t in NOTIFY_THRESHOLDS if pct >= t), default=None)
            if crossed is not None and crossed != notified.get(key):
                crossings.append((p.display_name, label, pct, crossed))
                notified[key] = crossed
            elif crossed is None and key in notified:
                del notified[key]
    return crossings


def display_quota(p: ProviderReport) -> dict | None:
    """Quota dict to use for the provider's headline %/reset in the menu bar.

    Claude's primary (via claude_quota_from_oauth) is already the 5-hour
    window. As a belt-and-suspenders fallback, if a report still has a
    top-level 7-day headline but includes a five_hour window, prefer that
    for the clock-adjacent glance — 5-hour is what blocks you next.
    """
    q = (p.meta or {}).get("quota") or {}
    if q.get("used_percent") is None and not q.get("windows"):
        return None
    if p.provider == ProviderId.CLAUDE:
        windows = q.get("windows") or []
        five_hour = next((w for w in windows if w.get("key") == "five_hour"), None)
        if five_hour and five_hour.get("used_percent") is not None:
            return {
                "used_percent": five_hour.get("used_percent"),
                "resets_at": five_hour.get("resets_at"),
                "label": five_hour.get("label") or "5-hour",
                "plan": q.get("plan"),
                "window_seconds": five_hour.get("window_seconds")
                or q.get("window_seconds")
                or 5 * 3600,
                "period_start": five_hour.get("period_start") or q.get("period_start"),
                "windows": windows,
            }
    return q if q.get("used_percent") is not None else None


def quota_of(p: ProviderReport) -> float | None:
    q = display_quota(p)
    pct = q.get("used_percent") if q else None
    if pct is None:
        return None
    try:
        return max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return None


def burn_of(p: ProviderReport) -> BurnProjection | None:
    """Burn-rate projection for the provider's *display* quota window.

    Computed at call time (not cached) so a menubar that reuses a snapshot
    still ages the "hits Fri" chip correctly as the clock moves.
    """
    q = display_quota(p)
    if not q:
        return None
    return project_from_quota(q)


def title_quota_chip(p: ProviderReport) -> str:
    """Clock title, e.g. ``Grok 64%``. Burn-rate lives in the menu + tooltip."""
    style = PROVIDER_STYLE.get(p.provider.value, {"letter": "?", "short": "AI"})
    short = str(style.get("short") or style.get("letter") or "?")
    pct = quota_of(p)
    if pct is None:
        return short
    return f"{short} {int(round(pct))}%"


def title_quota_tooltip(p: ProviderReport) -> str:
    """Hover text that spells out the clock chip."""
    style = PROVIDER_STYLE.get(p.provider.value, {"short": "AI"})
    short = str(style.get("short") or "AI")
    pct = quota_of(p)
    if pct is None:
        return short
    bits = [f"{short} {int(round(pct))}% used"]
    q = display_quota(p) or {}
    label = str(q.get("label") or "").replace(" limit", "").strip()
    if label:
        bits.append(label)
    burn = burn_of(p)
    if burn is not None and burn.summary:
        bits.append(burn.summary)
    elif burn is not None and burn.hits_label and burn.hits_label not in ("idle",):
        bits.append(burn.hits_label)
    return " · ".join(bits)


def find_provider(report: AggregateReport, pid: str) -> ProviderReport | None:
    for p in report.providers:
        if p.provider.value == pid:
            return p
    # codex card may be the merged openai/codex entry
    if pid == "openai":
        for p in report.providers:
            if p.provider.value in ("openai", "codex"):
                return p
    return None


# Providers remain visible for thirty minutes after observed usage increases.
ACTIVITY_TIMEOUT_SECONDS = 30 * 60


def active_report(
    report: AggregateReport,
    activity: dict,
    *,
    now: float,
    timeout: float = ACTIVITY_TIMEOUT_SECONDS,
) -> AggregateReport:
    """Track usage increases, not polling timestamps or quota resets.

    The first reading establishes a baseline, never evidence of activity.
    Persisted observations prevent restarts from reviving idle providers.
    Hidden providers are still collected so new usage brings them back.
    """
    visible = []
    for provider in report.providers:
        pid = provider.provider.value
        quota = (provider.meta or {}).get("quota") or {}
        readings: dict[str, float] = {
            "tokens": provider.total_tokens,
            "requests": provider.requests,
        }
        if provider.cost_usd is not None:
            readings["cost"] = provider.cost_usd
        windows = [
            ("primary", quota),
            *[
                (str(w.get("key") or w.get("label") or i), w)
                for i, w in enumerate(quota.get("windows") or [])
            ],
        ]
        for key, window in [] if provider.meta.get("quota_stale") else windows:
            try:
                value = float(window.get("used_percent"))
                if math.isfinite(value):
                    readings[f"quota:{key}"] = value
            except (TypeError, ValueError):
                pass
        previous = activity.get(pid)
        if not isinstance(previous, dict):
            previous = {}
        old = previous.get("readings", {})
        increased = any(key in old and value > max(0, old[key]) for key, value in readings.items())
        # Version 1 treated the initial balance as activity. Discard that
        # inferred timestamp while retaining its useful comparison baseline.
        last_active = (
            now
            if increased
            else (previous.get("last_active", 0) if previous.get("version") == 2 else 0)
        )
        # Missing data must not erase a baseline and manufacture activity on recovery.
        activity[pid] = {"version": 2, "readings": {**old, **readings}, "last_active": last_active}
        if last_active and 0 <= now - last_active < timeout:
            visible.append(provider)
    return report.model_copy(update={"providers": visible})


# ── colors ────────────────────────────────────────────────────────────


def brighten(rgb: RGB, factor: float = 1.22) -> RGB:
    """Lift brand colors for dark menus so bars don't sink into the chrome."""
    return (
        min(255, int(rgb[0] * factor)),
        min(255, int(rgb[1] * factor)),
        min(255, int(rgb[2] * factor)),
    )


def build_palette(dark: bool) -> Palette:
    """Resolve brand + heat colors for a light or dark menu.

    Takes `dark` as an argument rather than detecting it: appearance
    detection is the one AppKit-dependent step, so it stays in menubar.py
    and this stays testable on any platform.
    """
    brands: dict[str, RGB] = {k: v["rgb"] for k, v in PROVIDER_STYLE.items()}
    ok, warn, hot, crit = _RGB_OK, _RGB_WARN, _RGB_HOT, _RGB_CRIT
    empty = _RGB_EMPTY_DARK if dark else _RGB_EMPTY_LIGHT
    if dark:
        brands = {k: brighten(v) for k, v in brands.items()}
        ok, warn, hot, crit = brighten(ok), brighten(warn), brighten(hot), brighten(crit)
    return {
        "dark": dark,
        "brands": brands,
        "ok": ok,
        "warn": warn,
        "hot": hot,
        "crit": crit,
        "empty": empty,
    }


def pct_rgb(
    pct: float,
    brand: RGB,
    *,
    warn: RGB = _RGB_WARN,
    hot: RGB = _RGB_HOT,
    crit: RGB = _RGB_CRIT,
) -> RGB:
    """Keep each AI's brand color so bars stay identifiable.

    Only flip to the heat color when the quota is effectively gone (≥90%),
    otherwise every provider in the 50–70% band looks the same gold.
    `warn`/`hot` stay in the signature so existing call sites keep working.
    """
    del warn, hot
    if pct >= 90:
        return crit
    return brand


def bar_color_for_pct(
    pct: float,
    base_rgb: RGB,
    *,
    warn: RGB = _RGB_WARN,
    hot: RGB = _RGB_HOT,
    crit: RGB = _RGB_CRIT,
) -> RGB:
    return pct_rgb(pct, base_rgb, warn=warn, hot=hot, crit=crit)


def lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


# ── bars ──────────────────────────────────────────────────────────────


def unicode_bar(pct: float, width: int = 10) -> str:
    """Plain fallback bar (tests / non-AppKit)."""
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def bar_segments(
    pct: float,
    width: int,
    brand: RGB,
    *,
    empty: RGB = _RGB_EMPTY_LIGHT,
    warn: RGB = _RGB_WARN,
    hot: RGB = _RGB_HOT,
    crit: RGB = _RGB_CRIT,
) -> list[tuple[str, RGB]]:
    """Solid brand/heat fill; empty track matches light or dark menu."""
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    fill = pct_rgb(pct, brand, warn=warn, hot=hot, crit=crit)
    segs: list[tuple[str, RGB]] = []
    for i in range(width):
        segs.append(("█", fill) if i < filled else ("░", empty))
    return segs
