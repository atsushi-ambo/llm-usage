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
from pathlib import Path
from typing import TypedDict

from llm_usage.models import AggregateReport, ProviderId, ProviderReport
from llm_usage.quota import atomic_write_json, quota_windows

RGB = tuple[int, int, int]

PREFS_PATH = Path.home() / ".config" / "llm-usage" / "menubar.json"
NOTIFY_THRESHOLDS = (70, 90)

# Default: Grok in the menu bar (user can switch)
DEFAULT_FOCUS = "grok"

# VS Code Dark+-inspired palette — muted so bars stay legible on both
# light and dark NSMenus (background is always system-drawn).
# Brand rgb is the *light-menu* baseline; dark mode brightens ~18%.
PROVIDER_STYLE: dict[str, dict] = {
    "claude": {"letter": "C", "short": "Claude", "rgb": (206, 145, 120)},  # #ce9178
    "codex": {"letter": "O", "short": "Codex", "rgb": (106, 153, 85)},  # #6a9955
    "openai": {"letter": "O", "short": "OpenAI", "rgb": (78, 201, 176)},  # #4ec9b0
    "grok": {"letter": "G", "short": "Grok", "rgb": (197, 134, 192)},  # #c586c0
    "cursor": {"letter": "Cu", "short": "Cursor", "rgb": (86, 156, 214)},  # #569cd6
    "gemini": {"letter": "Ge", "short": "Gemini", "rgb": (204, 167, 0)},  # #cca700
    "openrouter": {"letter": "Or", "short": "OpenRouter", "rgb": (156, 220, 254)},  # #9cdcfe
    "cohere": {"letter": "Co", "short": "Cohere", "rgb": (0, 180, 216)},  # #00b4d8
    "mistral": {"letter": "Mi", "short": "Mistral", "rgb": (255, 107, 53)},  # #ff6b35
    "replicate": {"letter": "Re", "short": "Replicate", "rgb": (99, 102, 241)},  # #6366f1
    "huggingface": {"letter": "Hf", "short": "HuggingFace", "rgb": (255, 217, 61)},  # #ffd93d
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

# Charts heat ramp (light-menu baseline)
_RGB_OK: RGB = (137, 209, 133)  # #89d185
_RGB_WARN: RGB = (204, 167, 0)  # #cca700  ≥50%
_RGB_HOT: RGB = (209, 134, 22)  # #d18616  ≥70%
_RGB_CRIT: RGB = (229, 20, 0)  # #e51400   ≥90%
_RGB_EMPTY_LIGHT: RGB = (200, 200, 205)
_RGB_EMPTY_DARK: RGB = (60, 60, 60)


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


# ── colors ────────────────────────────────────────────────────────────


def brighten(rgb: RGB, factor: float = 1.18) -> RGB:
    """Lift colors ~15–20% for dark menus so they don't sink into the chrome."""
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
    """Brand while healthy; charts heat ramp at ≥50 / ≥70 / ≥90."""
    if pct >= 90:
        return crit
    if pct >= 70:
        return hot
    if pct >= 50:
        return warn
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
