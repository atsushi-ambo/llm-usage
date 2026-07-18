"""macOS menu bar — one colorful usage bar + switchable provider."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from llm_usage.config import load_settings
from llm_usage.models import AggregateReport, ProviderId, ProviderReport
from llm_usage.quota import atomic_write_json, quota_windows

# Poll gently — quota barely moves minute-to-minute. Less frequent = less RAM/CPU.
REFRESH_SECONDS = 300
# Menubar uses quota_only collection (no log scans); days only labels the period.
MENUBAR_DAYS = 1
# Reuse the light quota snapshot between polls.
MENUBAR_SNAPSHOT_TTL_S = 240.0
PREFS_PATH = Path.home() / ".config" / "llm-usage" / "menubar.json"
NOTIFY_THRESHOLDS = (70, 90)

# Default: Grok in the menu bar (user can switch)
DEFAULT_FOCUS = "grok"

# Gaming / neon HUD palette — bright fills that read on dark chrome.
PROVIDER_STYLE: dict[str, dict] = {
    # neon orange
    "claude": {"letter": "C", "short": "Claude", "rgb": (255, 122, 45)},
    # neon green
    "codex": {"letter": "X", "short": "Codex", "rgb": (57, 255, 20)},
    # matrix green
    "openai": {"letter": "O", "short": "OpenAI", "rgb": (0, 230, 118)},
    # electric magenta
    "grok": {"letter": "G", "short": "Grok", "rgb": (224, 64, 251)},
    # electric blue
    "cursor": {"letter": "Cu", "short": "Cursor", "rgb": (0, 180, 255)},
    # neon gold
    "gemini": {"letter": "Ge", "short": "Gemini", "rgb": (255, 214, 0)},
    # cyan laser
    "openrouter": {"letter": "Or", "short": "OpenRouter", "rgb": (0, 245, 255)},
}

FOCUS_ORDER = ["grok", "codex", "claude", "cursor", "gemini", "openrouter", "openai"]

# Usage heat — brand neon until high load, then warn → crit.
_RGB_OK = (57, 255, 20)  # neon green
_RGB_WARN = (255, 230, 0)  # warning yellow
_RGB_HOT = (255, 109, 0)  # hot orange
_RGB_CRIT = (255, 23, 68)  # alert red
_RGB_EMPTY = (48, 48, 64)  # dark HUD track
_RGB_MUTED = (140, 145, 170)  # cool grey
_RGB_TITLE = (236, 240, 255)  # near-white HUD text
_RGB_ACCENT = (0, 180, 255)  # electric blue
_RGB_LINK = (0, 200, 255)  # cyan link
_RGB_STRING = (255, 122, 45)  # neon orange


def _load_prefs() -> dict:
    if PREFS_PATH.exists():
        try:
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"focus": DEFAULT_FOCUS}


def _save_prefs(prefs: dict) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(PREFS_PATH, prefs)
    except OSError:
        pass


def _quota_crossings(
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


def _display_quota(p: ProviderReport) -> dict | None:
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


def _quota_of(p: ProviderReport) -> float | None:
    q = _display_quota(p)
    pct = q.get("used_percent") if q else None
    if pct is None:
        return None
    try:
        return max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return None


def _find_provider(report: AggregateReport, pid: str) -> ProviderReport | None:
    for p in report.providers:
        if p.provider.value == pid:
            return p
    # codex card may be the merged openai/codex entry
    if pid == "openai":
        for p in report.providers:
            if p.provider.value in ("openai", "codex"):
                return p
    return None


def _unicode_bar(pct: float, width: int = 10) -> str:
    """Plain fallback bar (tests / non-AppKit)."""
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _pct_rgb(pct: float, brand: tuple[int, int, int]) -> tuple[int, int, int]:
    """Keep brand color until high usage, then VS Code warn/error tones."""
    if pct >= 90:
        return _RGB_CRIT
    if pct >= 75:
        return _RGB_HOT
    return brand


def _bar_color_for_pct(pct: float, base_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return _pct_rgb(pct, base_rgb)


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _bar_segments(
    pct: float, width: int, brand: tuple[int, int, int]
) -> list[tuple[str, tuple[int, int, int]]]:
    """Neon bar: solid brand fill, heat toward red when the meter is hot."""
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    segs: list[tuple[str, tuple[int, int, int]]] = []
    for i in range(width):
        if i < filled:
            t = (i + 1) / max(width, 1)
            if pct >= 90:
                color = _lerp_rgb(brand, _RGB_CRIT, 0.4 + 0.6 * t)
            elif pct >= 75:
                color = _lerp_rgb(brand, _RGB_HOT, 0.3 + 0.55 * t)
            else:
                color = brand
            segs.append(("█", color))
        else:
            segs.append(("░", _RGB_EMPTY))
    return segs


def _ns_color(rgb: tuple[int, int, int], alpha: float = 1.0):
    from AppKit import NSColor  # type: ignore

    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


def _attributed_title(
    parts: list[tuple[str, tuple[int, int, int] | None]],
    *,
    size: float = 13.0,
):
    """Build a multi-color NSAttributedString for an NSMenuItem title."""
    try:
        from AppKit import (  # type: ignore
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableAttributedString,
        )
    except ImportError:
        return None

    font = NSFont.menuFontOfSize_(size)
    attr = NSMutableAttributedString.alloc().initWithString_("")
    for text, rgb in parts:
        if not text:
            continue
        color = _ns_color(rgb) if rgb is not None else _ns_color(_RGB_TITLE)
        chunk = NSMutableAttributedString.alloc().initWithString_attributes_(
            text,
            {
                NSForegroundColorAttributeName: color,
                NSFontAttributeName: font,
            },
        )
        attr.appendAttributedString_(chunk)
    return attr


def _set_colored_title(
    item, parts: list[tuple[str, tuple[int, int, int] | None]], plain: str
) -> None:
    """Apply attributed title when AppKit is available; else plain string."""
    item.title = plain
    attr = _attributed_title(parts)
    if attr is None:
        return
    try:
        ns = getattr(item, "_menuitem", None)
        if ns is not None:
            ns.setAttributedTitle_(attr)
    except Exception:  # noqa: BLE001
        pass


def _force_dark_appearance() -> None:
    """Force this app's menus into dark mode (VS Code–like chrome).

    System light mode otherwise paints a white menu under our Dark+ colors,
    which makes them look washed out / unchanged.
    """
    try:
        from AppKit import NSAppearance, NSApplication  # type: ignore

        app = NSApplication.sharedApplication()
        dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        if dark is not None and app is not None:
            app.setAppearance_(dark)
    except Exception:  # noqa: BLE001
        pass


def _render_single_bar_icon(pct: float, rgb: tuple[int, int, int]) -> Path | None:
    """Rounded usage pill for the menu bar — brand fill that heats up at high %."""
    try:
        from AppKit import (  # type: ignore
            NSBezierPath,
            NSBitmapImageRep,
            NSCalibratedRGBColorSpace,
            NSColor,
            NSDeviceRGBColorSpace,
            NSGraphicsContext,
            NSImage,
            NSPNGFileType,
        )
        from Foundation import NSMakeRect  # type: ignore
    except ImportError:
        return None

    scale = 2
    pt_w, pt_h = 28, 13
    px_w, px_h = pt_w * scale, pt_h * scale

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px_w, px_h, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    if rep is None:
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, px_w, px_h, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
        )
    if rep is None:
        return None

    img = NSImage.alloc().initWithSize_((pt_w, pt_h))
    img.addRepresentation_(rep)
    img.lockFocus()
    try:
        ctx = NSGraphicsContext.currentContext()
        if ctx is not None:
            ctx.setShouldAntialias_(True)

        NSColor.clearColor().set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, pt_w, pt_h))

        pad = 0.5
        bar_h = pt_h - 2 * pad
        track_w = pt_w - 2 * pad
        y = pad
        radius = bar_h / 2

        # Dark HUD track
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.10, 0.14, 1.0).set()
        track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(pad, y, track_w, bar_h), radius, radius
        )
        track.fill()
        # Neon-ish edge using brand color at low alpha
        br, bg, bb = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
        NSColor.colorWithCalibratedRed_green_blue_alpha_(br, bg, bb, 0.45).set()
        track.setLineWidth_(1.0)
        track.stroke()

        fill_rgb = _bar_color_for_pct(pct, rgb)
        r, g, b = fill_rgb[0] / 255.0, fill_rgb[1] / 255.0, fill_rgb[2] / 255.0
        if pct > 0:
            fill_w = max(bar_h * 0.95, track_w * (pct / 100.0))
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
            fill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad, y, min(fill_w, track_w), bar_h), radius, radius
            )
            fill.fill()
            # Bright specular for "lit LED" feel
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.28).set()
            hi = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad + 1, y + bar_h * 0.15, min(fill_w, track_w) - 2, bar_h * 0.32),
                2,
                2,
            )
            hi.fill()
    finally:
        img.unlockFocus()

    tdir = Path(tempfile.gettempdir()) / "llm-usage-menubar"
    tdir.mkdir(exist_ok=True)
    out = tdir / "status.png"
    try:
        tiff = img.TIFFRepresentation()
        if tiff is None:
            return None
        rep2 = NSBitmapImageRep.imageRepWithData_(tiff)
        if rep2 is None:
            return None
        data = rep2.representationUsingType_properties_(NSPNGFileType, None)
        if data is None:
            return None
        data.writeToFile_atomically_(str(out), True)
        return out
    except Exception:
        return None


def _collect_menubar_report(
    *,
    days: int,
    ttl_s: float,
    force_refresh: bool,
) -> AggregateReport:
    """Quota-only collect for the menubar — no local log scans.

    Skips Claude/Codex/Grok session JSONL walks, OpenAI org usage series,
    Gemini log scans, and model-list API calls. Only hits the small
    subscription/credit endpoints the % bars need, then slims the result
    so the long-lived process keeps almost nothing in RAM between polls.
    """
    from llm_usage.config import load_settings
    from llm_usage.providers import collect_all_cached
    from llm_usage.serialize import slim_report_for_menubar

    settings = load_settings()
    report = collect_all_cached(
        settings,
        days=days,
        ttl_s=ttl_s,
        force_refresh=force_refresh,
        quota_only=True,
    )
    return slim_report_for_menubar(report)


def run_menubar() -> None:
    """Start the menu bar app (blocks). Requires rumps on macOS."""
    try:
        import rumps  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Menu bar requires 'rumps' (macOS only). Reinstall from the repo root:\n"
            "  uv tool install --force -e .\n"
            f"({exc})"
        ) from exc

    settings = load_settings()
    prefs = _load_prefs()
    state: dict = {
        "report": None,
        "error": None,
        "updating": False,
        "focus": prefs.get("focus") or DEFAULT_FOCUS,
        # Skip re-rendering the status-bar icon when the painted key is unchanged.
        "status_key": None,
    }

    app = rumps.App("llm-usage", title="…", quit_button=None)
    # Dark menu chrome so VS Code Dark+ colors read correctly (not washed on white).
    _force_dark_appearance()

    # Keep strong refs so callbacks aren't GC'd
    callbacks: list = []

    def noop(_=None) -> None:
        """Enabled menu rows need a callback so macOS doesn't gray them out."""
        return None

    def set_focus(pid: str) -> None:
        state["focus"] = pid
        prefs["focus"] = pid
        _save_prefs(prefs)
        report = state.get("report")
        if report is not None:
            _apply_status(app, report, state["focus"])
            rebuild_menu(report)

    def _apply_status(app_obj, report: AggregateReport, focus: str) -> None:
        p = _find_provider(report, focus)
        # Prefer focus; if no quota, fall back to grok then first with quota
        if p is None or _quota_of(p) is None:
            for candidate in [focus, DEFAULT_FOCUS, "codex", "claude"]:
                p = _find_provider(report, candidate)
                if p is not None and _quota_of(p) is not None:
                    focus = candidate
                    break
            else:
                p = None

        if p is None:
            pct = None
        else:
            pct = _quota_of(p)

        style = PROVIDER_STYLE.get(
            focus if p else DEFAULT_FOCUS,
            {"letter": "?", "short": "AI", "rgb": (140, 150, 160)},
        )
        rgb = style["rgb"]

        if pct is None:
            key = f"{focus}:none"
            if state.get("status_key") != key:
                state["status_key"] = key
                app_obj.title = f" {style['letter']}"
                try:
                    app_obj.icon = None
                except Exception:
                    pass
            return

        rounded = int(round(pct))
        key = f"{focus}:{rounded}:{style['letter']}"
        # Title string is cheap; only re-render the PNG icon when the bar moved.
        app_obj.title = f" {style['letter']}{rounded}%"
        if state.get("status_key") == key:
            return
        state["status_key"] = key

        path = _render_single_bar_icon(pct, rgb)
        if path and path.exists():
            try:
                app_obj.template = False
                app_obj.icon = str(path)
            except Exception:
                pass

    def rebuild_menu(report: AggregateReport | None, error: str | None = None) -> None:
        app.menu.clear()
        callbacks.clear()

        def add_enabled(
            title: str,
            callback=None,
            checked: bool = False,
            *,
            parts: list[tuple[str, tuple[int, int, int] | None]] | None = None,
        ) -> rumps.MenuItem:
            item = rumps.MenuItem(title)
            cb = callback or noop
            item.set_callback(cb)
            if checked:
                item.state = 1
            if parts:
                _set_colored_title(item, parts, title)
            app.menu.add(item)
            callbacks.append(item)
            return item

        if error:
            add_enabled(
                f"! {error[:70]}",
                parts=[("! ", _RGB_HOT), (error[:70], _RGB_CRIT)],
            )

        if report is None:
            add_enabled(
                "Loading…",
                parts=[("Loading…", _RGB_MUTED)],
            )
        else:
            stamp = (
                f"Updated {datetime.now().strftime('%H:%M:%S')}  ·  "
                f"{report.period_start} → {report.period_end}"
            )
            add_enabled(stamp, parts=[(stamp, _RGB_MUTED)])
            app.menu.add(None)

            # ── Per-provider usage (colored bars, no emoji) ──
            for p in report.providers:
                pct = _quota_of(p)
                style = PROVIDER_STYLE.get(
                    p.provider.value,
                    {"letter": "?", "rgb": (120, 140, 160)},
                )
                brand = style["rgb"]
                letter = style.get("letter") or "?"
                if pct is not None:
                    q = _display_quota(p) or {}
                    plan = q.get("plan") or ""
                    label = (q.get("label") or "").replace(" limit", "").strip()
                    pct_rgb = _pct_rgb(pct, brand)
                    bar_segs = _bar_segments(pct, 10, brand)
                    plain_bar = "".join(c for c, _ in bar_segs)

                    line = f"{letter}  {p.display_name}  {plain_bar}  {pct:.0f}%"
                    if label:
                        line += f"  ·  {label}"
                    if plan:
                        line += f"  ·  {plan}"

                    parts: list[tuple[str, tuple[int, int, int] | None]] = [
                        (f"{letter}  ", brand),
                        (f"{p.display_name}  ", brand),
                    ]
                    parts.extend(bar_segs)
                    parts.append((f"  {pct:.0f}%", pct_rgb))
                    if label:
                        parts.append((f"  ·  {label}", _RGB_MUTED))
                    if plan:
                        parts.append((f"  ·  {plan}", brand))

                    item = add_enabled(line, parts=parts)

                    for w in q.get("windows") or []:
                        if w.get("used_percent") is None:
                            continue
                        wp = float(w["used_percent"])
                        w_brand = brand
                        w_segs = _bar_segments(wp, 8, w_brand)
                        w_plain = "".join(c for c, _ in w_segs)
                        w_label = str(w.get("label") or "window")
                        w_line = f"    {w_label}  {w_plain}  {wp:.0f}%"
                        w_parts: list[tuple[str, tuple[int, int, int] | None]] = [
                            (f"    {w_label}  ", _RGB_MUTED),
                            *w_segs,
                            (f"  {wp:.0f}%", _pct_rgb(wp, w_brand)),
                        ]
                        sub = rumps.MenuItem(w_line)
                        sub.set_callback(noop)
                        _set_colored_title(sub, w_parts, w_line)
                        item.add(sub)
                        callbacks.append(sub)
                    reset = q.get("resets_at")
                    if reset:
                        try:
                            d = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
                            reset_txt = (
                                f"    {label} resets {d.strftime('%b %d, %H:%M')}"
                                if label
                                else f"    Resets {d.strftime('%b %d, %H:%M')}"
                            )
                            sub = rumps.MenuItem(reset_txt)
                            sub.set_callback(noop)
                            _set_colored_title(
                                sub, [(reset_txt, _RGB_MUTED)], reset_txt
                            )
                            item.add(sub)
                            callbacks.append(sub)
                        except ValueError:
                            pass
                elif p.requests or p.total_tokens:
                    cost = f"  ·  ${p.cost_usd:.2f}" if p.cost_usd is not None else ""
                    line = (
                        f"{letter}  {p.display_name}  ·  "
                        f"{p.requests:,} req  ·  {p.total_tokens:,} tok{cost}"
                    )
                    add_enabled(
                        line,
                        parts=[
                            (f"{letter}  ", brand),
                            (f"{p.display_name}  ·  ", brand),
                            (
                                f"{p.requests:,} req  ·  {p.total_tokens:,} tok{cost}",
                                _RGB_MUTED,
                            ),
                        ],
                    )
                else:
                    line = f"{letter}  {p.display_name}  ·  not configured"
                    add_enabled(
                        line,
                        parts=[
                            (f"{letter}  ", _RGB_MUTED),
                            (f"{p.display_name}  ·  ", _RGB_MUTED),
                            ("not configured", _RGB_EMPTY),
                        ],
                    )

            app.menu.add(None)

            # ── Switch which bar shows in the menu bar ──
            focus_menu = rumps.MenuItem("Show in menu bar")
            focus_menu.set_callback(noop)
            _set_colored_title(
                focus_menu,
                [("Show in menu bar", _RGB_LINK)],
                "Show in menu bar",
            )
            callbacks.append(focus_menu)

            for pid in FOCUS_ORDER:
                prov = _find_provider(report, pid)
                if prov is None:
                    continue
                style = PROVIDER_STYLE.get(
                    pid, {"short": pid, "letter": "?", "rgb": _RGB_MUTED}
                )
                brand = style["rgb"]
                letter = style.get("letter") or "?"
                pct = _quota_of(prov)
                if pct is not None:
                    label = f"{letter}  {style['short']}  ·  {pct:.0f}%"
                    parts = [
                        (f"{letter}  ", brand),
                        (f"{style['short']}  ·  ", brand),
                        (f"{pct:.0f}%", _pct_rgb(pct, brand)),
                    ]
                elif prov.source.value == "unavailable" and not (
                    prov.requests or prov.total_tokens
                ):
                    label = f"{letter}  {style['short']}  ·  n/a"
                    parts = [
                        (f"{letter}  ", _RGB_MUTED),
                        (f"{style['short']}  ·  n/a", _RGB_EMPTY),
                    ]
                else:
                    label = f"{letter}  {style['short']}"
                    parts = [(f"{letter}  ", brand), (style["short"], brand)]

                def _make_cb(provider_id: str):
                    def _cb(_=None, _pid=provider_id) -> None:
                        set_focus(_pid)

                    return _cb

                sub = rumps.MenuItem(label)
                sub.set_callback(_make_cb(pid))
                _set_colored_title(sub, parts, label)
                if state["focus"] == pid or (
                    state["focus"] == "openai" and pid == "codex"
                ):
                    sub.state = 1
                if state["focus"] == pid:
                    sub.state = 1
                focus_menu.add(sub)
                callbacks.append(sub)

            app.menu.add(focus_menu)
            callbacks.append(focus_menu)

            costs = [p.cost_usd for p in report.providers if p.cost_usd is not None]
            if costs:
                app.menu.add(None)
                cost_line = f"Known cost: ${sum(costs):.2f}"
                add_enabled(
                    cost_line,
                    parts=[
                        ("Known cost: ", _RGB_MUTED),
                        (f"${sum(costs):.2f}", _RGB_WARN),
                    ],
                )

        app.menu.add(None)

        open_dash = rumps.MenuItem("Open Dashboard")
        refresh_item = rumps.MenuItem("Refresh Now")
        quit_item = rumps.MenuItem("Quit llm-usage")
        _set_colored_title(
            open_dash,
            [("Open Dashboard", _RGB_LINK)],
            "Open Dashboard",
        )
        _set_colored_title(
            refresh_item,
            [("Refresh Now", _RGB_OK)],
            "Refresh Now",
        )
        _set_colored_title(
            quit_item,
            [("Quit llm-usage", _RGB_MUTED)],
            "Quit llm-usage",
        )

        def _authenticated_dashboard_url() -> str | None:
            """URL for an already-running dashboard, with its token if we can
            find it (the dashboard writes its session to a 0600 cache file
            on start; see llm_usage.quota.write_dashboard_session)."""
            try:
                import httpx

                r = httpx.get(
                    f"http://{settings.host}:{settings.port}/api/health",
                    timeout=1.0,
                )
                if r.status_code != 200:
                    return None
            except Exception:  # noqa: BLE001
                return None

            from llm_usage.quota import read_dashboard_session

            session = read_dashboard_session()
            if (
                session
                and session.get("host") == settings.host
                and session.get("port") == settings.port
                and session.get("token")
            ):
                return f"http://{settings.host}:{settings.port}/?token={session['token']}"
            return f"http://{settings.host}:{settings.port}/"

        def _open_dashboard(_=None) -> None:
            def _run() -> None:
                url = _authenticated_dashboard_url()
                if url:
                    webbrowser.open(url)
                    return
                subprocess.Popen(
                    [
                        "llm-usage",
                        "dashboard",
                        "--host",
                        settings.host,
                        "--port",
                        str(settings.port),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                time.sleep(1.5)
                webbrowser.open(
                    _authenticated_dashboard_url()
                    or f"http://{settings.host}:{settings.port}/"
                )

            threading.Thread(target=_run, daemon=True).start()

        def _refresh(_=None) -> None:
            app.title = " …"
            threading.Thread(
                target=lambda: do_collect(force_refresh=True), daemon=True
            ).start()

        def _quit(_=None) -> None:
            rumps.quit_application()

        open_dash.set_callback(_open_dashboard)
        refresh_item.set_callback(_refresh)
        quit_item.set_callback(_quit)
        app.menu.add(open_dash)
        app.menu.add(refresh_item)
        app.menu.add(None)
        app.menu.add(quit_item)
        callbacks.extend([open_dash, refresh_item, quit_item])

    # AppKit (app.title/app.icon/app.menu) is not safe to touch from a
    # background thread. do_collect() runs there and only does blocking I/O
    # (collect_all hits real provider APIs); it hands the result off via
    # `pending` instead of mutating the UI directly. A rumps.Timer — which
    # fires on the main thread as part of the NSApplication run loop — picks
    # the result up and is the only place that mutates AppKit state.
    pending_lock = threading.Lock()
    pending: dict = {"report": None, "error": None, "ready": False}
    # (provider, window label) -> highest threshold already notified for,
    # so a poll landing again at the same level doesn't refire. Cleared
    # once the window drops back below the lowest threshold (e.g. it
    # reset), so a future crossing notifies again.
    notified: dict[tuple[str, str], int] = {}

    def _check_quota_notifications(report: AggregateReport) -> None:
        for name, label, pct, threshold in _quota_crossings(report, notified):
            try:
                rumps.notification(
                    title=f"{name} — {label}",
                    subtitle=f"{pct:.0f}% used",
                    message="Almost at your usage limit."
                    if threshold >= 90
                    else "Approaching your usage limit.",
                )
            except Exception:  # noqa: BLE001
                pass  # notifications are best-effort, never fatal

    def do_collect(*, force_refresh: bool = False) -> None:
        if state["updating"]:
            return
        state["updating"] = True
        try:
            # Collect in a short-lived child process so httpx/pydantic/log
            # trees are freed when it exits — the menubar UI process only
            # keeps a slim AggregateReport between polls.
            report = _collect_menubar_report(
                days=MENUBAR_DAYS,
                ttl_s=MENUBAR_SNAPSHOT_TTL_S,
                force_refresh=force_refresh,
            )
            with pending_lock:
                pending["report"] = report
                pending["error"] = None
                pending["ready"] = True
        except Exception as exc:  # noqa: BLE001
            with pending_lock:
                pending["error"] = str(exc)
                pending["ready"] = True
        finally:
            state["updating"] = False

    def _apply_pending_update(_timer=None) -> None:
        with pending_lock:
            if not pending["ready"]:
                return
            report, error = pending["report"], pending["error"]
            pending["report"] = None  # drop extra ref; state owns the report
            pending["error"] = None
            pending["ready"] = False

        if error is not None:
            state["error"] = error
            app.title = " !"
            rebuild_menu(state.get("report"), error=error)
        else:
            state["report"] = report
            state["error"] = None
            _apply_status(app, report, state["focus"])
            rebuild_menu(report)
            _check_quota_notifications(report)

    def background_loop() -> None:
        while True:
            do_collect()
            time.sleep(REFRESH_SECONDS)

    rebuild_menu(None)
    threading.Thread(target=background_loop, daemon=True).start()
    rumps.Timer(_apply_pending_update, 1).start()
    app.run()
