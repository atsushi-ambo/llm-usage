"""macOS menu bar — one colorful usage bar + switchable provider.

Only the AppKit/rumps glue lives here (NSColor, NSImage, timers, threads),
which is why pyright skips this file. The decision logic it draws from —
quota selection, palette resolution, bar geometry — is AppKit-free and
lives in `menubar_core`, where it is type-checked and unit-tested.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from llm_usage.config import load_settings
from llm_usage.menubar_core import (
    active_report,
    DEFAULT_FOCUS,
    FOCUS_ORDER,
    NOTIFY_THRESHOLDS,
    PREFS_PATH,
    PROVIDER_STYLE,
    _RGB_CRIT,
    _RGB_EMPTY_LIGHT,
    _RGB_HOT,
    _RGB_WARN,
    bar_color_for_pct,
    bar_segments,
    brighten,
    build_palette,
    burn_of,
    display_quota,
    find_provider,
    lerp_rgb,
    load_prefs,
    pct_rgb,
    quota_crossings,
    quota_of,
    save_prefs,
    title_quota_chip,
    title_quota_tooltip,
    unicode_bar,
)
from llm_usage.models import AggregateReport

# Poll gently — quota barely moves minute-to-minute. Less frequent = less RAM/CPU.
REFRESH_SECONDS = 300
# Menubar uses quota_only collection (no log scans); days only labels the period.
MENUBAR_DAYS = 1
# Reuse the light quota snapshot between polls.
MENUBAR_SNAPSHOT_TTL_S = 240.0

# Private aliases keep this module's existing call sites (and tests that
# import from `llm_usage.menubar`) working after the split.
_load_prefs = load_prefs
_save_prefs = save_prefs
_quota_crossings = quota_crossings
_display_quota = display_quota
_quota_of = quota_of
_burn_of = burn_of
_title_quota_chip = title_quota_chip
_title_quota_tooltip = title_quota_tooltip
_find_provider = find_provider
_unicode_bar = unicode_bar
_brighten = brighten
_pct_rgb = pct_rgb
_bar_color_for_pct = bar_color_for_pct
_lerp_rgb = lerp_rgb
_bar_segments = bar_segments

__all__ = [
    "DEFAULT_FOCUS",
    "FOCUS_ORDER",
    "MENUBAR_DAYS",
    "NOTIFY_THRESHOLDS",
    "PREFS_PATH",
    "PROVIDER_STYLE",
    "REFRESH_SECONDS",
    "run_menubar",
]


def _is_dark_appearance() -> bool:
    """Whether the running app's effective appearance is dark."""
    try:
        from AppKit import NSApplication  # type: ignore

        app = NSApplication.sharedApplication()
        appearance = app.effectiveAppearance()
        match = appearance.bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameDarkAqua", "NSAppearanceNameAqua"]
        )
        return str(match) == "NSAppearanceNameDarkAqua"
    except Exception:  # noqa: BLE001
        return False


def _appearance_palette() -> dict:
    """Resolve brand + heat colors for the current light/dark menu.

    Appearance detection is the only AppKit-dependent step; the color math
    itself lives in menubar_core.build_palette().
    """
    from AppKit import NSColor

    pal = dict(build_palette(_is_dark_appearance()))

    def rgb(color):
        color = color.colorUsingColorSpaceName_("NSCalibratedRGBColorSpace")
        return tuple(
            round(c * 255)
            for c in (color.redComponent(), color.greenComponent(), color.blueComponent())
        )

    accent = rgb(NSColor.controlAccentColor())
    pal["brands"] = {pid: accent for pid in PROVIDER_STYLE}
    pal.update(
        ok=accent,
        warn=rgb(NSColor.systemOrangeColor()),
        hot=rgb(NSColor.systemOrangeColor()),
        crit=rgb(NSColor.systemRedColor()),
        empty=pal["empty"],
    )
    return pal


def _ns_color(rgb: tuple[int, int, int] | None, alpha: float = 1.0):
    from AppKit import NSColor  # type: ignore

    if rgb is None:
        # System menu label — adapts to light/dark automatically.
        return NSColor.labelColor()
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


def _attributed_title(
    parts: list[tuple[str, tuple[int, int, int] | None]],
    *,
    size: float = 13.0,
    menubar: bool = False,
):
    """Build a multi-color NSAttributedString.

    rgb=None → system label color (chrome text).
    """
    try:
        from AppKit import (  # type: ignore
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableAttributedString,
        )
    except ImportError:
        return None

    font = NSFont.menuBarFontOfSize_(size) if menubar else NSFont.menuFontOfSize_(size)
    attr = NSMutableAttributedString.alloc().initWithString_("")
    for text, rgb in parts:
        if not text:
            continue
        chunk = NSMutableAttributedString.alloc().initWithString_attributes_(
            text,
            {
                NSForegroundColorAttributeName: _ns_color(rgb),
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


def _set_status_item_title(
    app_obj,
    plain: str,
    parts: list[tuple[str, tuple[int, int, int] | None]],
) -> None:
    """Color the clock-adjacent title; rumps' title setter is system-only."""
    app_obj.title = plain
    attr = _attributed_title(parts, size=13.0, menubar=True)
    if attr is None:
        return
    try:
        nsapp = getattr(app_obj, "_nsapp", None)
        nsitem = getattr(nsapp, "nsstatusitem", None) if nsapp is not None else None
        if nsitem is None:
            return
        button = nsitem.button() if hasattr(nsitem, "button") else None
        if button is not None:
            button.setAttributedTitle_(attr)
        else:
            nsitem.setAttributedTitle_(attr)
    except Exception:  # noqa: BLE001
        pass


def _set_status_tooltip(app_obj, text: str) -> None:
    try:
        nsapp = getattr(app_obj, "_nsapp", None)
        nsitem = getattr(nsapp, "nsstatusitem", None) if nsapp is not None else None
        if nsitem is None:
            return
        button = nsitem.button() if hasattr(nsitem, "button") else None
        if button is not None:
            button.setToolTip_(text)
        else:
            nsitem.setToolTip_(text)
    except Exception:  # noqa: BLE001
        pass


def _render_single_bar_icon(
    pct: float,
    rgb: tuple[int, int, int],
    *,
    empty: tuple[int, int, int] = _RGB_EMPTY_LIGHT,
    warn: tuple[int, int, int] = _RGB_WARN,
    hot: tuple[int, int, int] = _RGB_HOT,
    crit: tuple[int, int, int] = _RGB_CRIT,
    filename: str = "status.png",
    pt_w: int = 24,
    pt_h: int = 8,
) -> Path | None:
    """Rounded usage pill for the status item and popup rows."""
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

        er, eg, eb = empty[0] / 255.0, empty[1] / 255.0, empty[2] / 255.0
        NSColor.colorWithCalibratedRed_green_blue_alpha_(er, eg, eb, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(pad, y, track_w, bar_h), radius, radius
        ).fill()

        fill_rgb = _bar_color_for_pct(pct, rgb, warn=warn, hot=hot, crit=crit)
        r, g, b = fill_rgb[0] / 255.0, fill_rgb[1] / 255.0, fill_rgb[2] / 255.0
        if pct > 0:
            fill_w = max(bar_h * 0.95, track_w * (pct / 100.0))
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad, y, min(fill_w, track_w), bar_h), radius, radius
            ).fill()
    finally:
        img.unlockFocus()

    tdir = Path(tempfile.gettempdir()) / "llm-usage-menubar"
    tdir.mkdir(exist_ok=True)
    out = tdir / filename
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
    # Use native menu typography and semantic system colors.
    activity = prefs.get("activity", {})
    if not isinstance(activity, dict):
        activity = {}

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
        if p is None:
            p = next(iter(report.providers), None)
        if p is not None:
            focus = p.provider.value
        state["display_focus"] = focus if p else None
        nsapp = getattr(app_obj, "_nsapp", None)
        status_item = getattr(nsapp, "nsstatusitem", None)
        if status_item is not None:
            status_item.setVisible_(p is not None)

        if p is None:
            pct = None
        else:
            pct = _quota_of(p)

        style = PROVIDER_STYLE.get(
            focus if p else DEFAULT_FOCUS,
            {"letter": "?", "short": "AI", "rgb": (140, 150, 160)},
        )
        pal = _appearance_palette()
        pid_key = focus if p else DEFAULT_FOCUS
        rgb = pal["brands"].get(pid_key, style["rgb"])

        letter = style.get("letter") or "?"
        if pct is None:
            key = f"{focus}:none:{pal['dark']}"
            if state.get("status_key") != key:
                state["status_key"] = key
                # System color — NES brights are unreadable in the clock strip.
                app_obj.title = f" {_title_quota_chip(p)}" if p else ""
                _set_status_tooltip(app_obj, "")
                try:
                    app_obj.icon = None
                except Exception:
                    pass
            return

        chip = _title_quota_chip(p) if p is not None else f"{letter} {int(round(pct))}%"
        key = f"{focus}:{chip}:{pal['dark']}:v3"
        app_obj.title = f" {chip}"
        if p is not None:
            _set_status_tooltip(app_obj, _title_quota_tooltip(p))
        if state.get("status_key") == key:
            return
        state["status_key"] = key

        path = _render_single_bar_icon(
            pct,
            rgb,
            empty=pal["empty"],
            warn=pal["warn"],
            hot=pal["hot"],
            crit=pal["crit"],
            filename="status.png",
        )
        if path and path.exists():
            try:
                app_obj.template = False
                app_obj.icon = str(path)
            except Exception:
                pass

    def rebuild_menu(report: AggregateReport | None, error: str | None = None) -> None:
        app.menu.clear()
        callbacks.clear()
        pal = _appearance_palette()

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
            # Only set attributed titles when we need mixed system + accent colors.
            if parts:
                _set_colored_title(item, parts, title)
            app.menu.add(item)
            callbacks.append(item)
            return item

        if error:
            # System label + red error accent on the message only.
            add_enabled(
                f"! {error[:70]}",
                parts=[("! ", None), (error[:70], pal["crit"])],
            )

        if report is None:
            add_enabled("Loading…")  # plain system color
        else:
            add_enabled(f"Updated {datetime.now().strftime('%H:%M')}")
            app.menu.add(None)

            # ── Per-provider: system labels, colored bar + % only ──
            for p in report.providers:
                pct = _quota_of(p)
                style = PROVIDER_STYLE.get(
                    p.provider.value,
                    {"letter": "?", "rgb": (120, 140, 160)},
                )
                brand = pal["brands"].get(p.provider.value, style["rgb"])
                letter = style.get("letter") or "?"
                if pct is not None:
                    q = _display_quota(p) or {}
                    plan = q.get("plan") or ""
                    label = (q.get("label") or "").replace(" limit", "").strip()
                    burn = _burn_of(p)
                    short = style.get("short", p.display_name)
                    line = f"{short}    {pct:.0f}%"
                    parts = [(f"{short}    ", None), (f"{pct:.0f}%", None)]

                    item = add_enabled(line, parts=parts)
                    bar_img = _render_single_bar_icon(
                        pct,
                        brand,
                        empty=pal["empty"],
                        warn=pal["warn"],
                        hot=pal["hot"],
                        crit=pal["crit"],
                        filename=f"menu-{p.provider.value}.png",
                        pt_w=30,
                        pt_h=6,
                    )
                    if bar_img and bar_img.exists():
                        try:
                            item.set_icon(str(bar_img), dimensions=(30, 6), template=False)
                        except Exception:  # noqa: BLE001
                            pass

                    for w in q.get("windows") or []:
                        if w.get("used_percent") is None:
                            continue
                        wp = float(w["used_percent"])
                        w_label = str(w.get("label") or "Usage")
                        w_line = f"{w_label}    {wp:.0f}%"
                        w_parts = [(w_line, None)]
                        sub = rumps.MenuItem(w_line)
                        sub.set_callback(noop)
                        _set_colored_title(sub, w_parts, w_line)
                        item.add(sub)
                        callbacks.append(sub)
                    if plan:
                        item.add(rumps.MenuItem(f"Plan: {plan}"))
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
                            item.add(sub)
                            callbacks.append(sub)
                        except ValueError:
                            pass
                    if burn is not None and burn.summary:
                        burn_sub = rumps.MenuItem(f"    {burn.summary}")
                        burn_sub.set_callback(noop)
                        item.add(burn_sub)
                        callbacks.append(burn_sub)
                elif p.requests or p.total_tokens:
                    cost = f"  ·  ${p.cost_usd:.2f}" if p.cost_usd is not None else ""
                    add_enabled(
                        f"{letter}  {p.display_name}  ·  "
                        f"{p.requests:,} req  ·  {p.total_tokens:,} tok{cost}",
                        parts=[
                            (f"{letter}  ", brand),
                            (f"{p.display_name}", None),
                            (
                                f"  ·  {p.requests:,} req  ·  {p.total_tokens:,} tok{cost}",
                                None,
                            ),
                        ],
                    )
            app.menu.add(None)

            # ── Switch which bar shows in the menu bar ──
            focus_menu = rumps.MenuItem("Show in menu bar")
            focus_menu.set_callback(noop)
            callbacks.append(focus_menu)

            for prov in report.providers:
                pid = prov.provider.value
                style = PROVIDER_STYLE.get(
                    pid, {"short": pid, "letter": "?", "rgb": (120, 140, 160)}
                )
                brand = pal["brands"].get(pid, style["rgb"])
                letter = style.get("letter") or "?"
                pct = _quota_of(prov)
                label = str(style["short"])
                if pct is not None:
                    label += f"    {pct:.0f}%"
                parts = None

                def _make_cb(provider_id: str):
                    def _cb(_=None, _pid=provider_id) -> None:
                        set_focus(_pid)

                    return _cb

                sub = rumps.MenuItem(label)
                sub.set_callback(_make_cb(pid))
                if parts:
                    _set_colored_title(sub, parts, label)
                if state.get("display_focus") == pid or (
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
                add_enabled(f"Known cost: ${sum(costs):.2f}")

        app.menu.add(None)

        # Chrome actions: plain system label color (no rainbow links).
        open_dash = rumps.MenuItem("Open Dashboard")
        refresh_item = rumps.MenuItem("Refresh Now")
        quit_item = rumps.MenuItem("Quit llm-usage")

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
                    _authenticated_dashboard_url() or f"http://{settings.host}:{settings.port}/"
                )

            threading.Thread(target=_run, daemon=True).start()

        def _refresh(_=None) -> None:
            app.title = " …"
            threading.Thread(target=lambda: do_collect(force_refresh=True), daemon=True).start()

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
                    subtitle=f"{pct:.0f}% used",  # burn detail is in the menu; keep notify short
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
            # Collect on a background thread (quota_only + slim_report) so
            # the rumps UI stays responsive; results land via pending + the
            # main-thread timer (_apply_pending_update).
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
            report = active_report(report, activity, now=time.time())
            prefs["activity"] = activity
            _save_prefs(prefs)
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
