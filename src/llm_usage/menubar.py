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
from llm_usage.models import AggregateReport, ProviderReport
from llm_usage.providers import collect_all
from llm_usage.quota import atomic_write_json

REFRESH_SECONDS = 120
PREFS_PATH = Path.home() / ".config" / "llm-usage" / "menubar.json"

# Default: Grok in the menu bar (user can switch)
DEFAULT_FOCUS = "grok"

PROVIDER_STYLE: dict[str, dict] = {
    "claude": {"letter": "C", "short": "Claude", "rgb": (212, 162, 127)},
    "codex": {"letter": "X", "short": "Codex", "rgb": (34, 197, 94)},
    "openai": {"letter": "O", "short": "OpenAI", "rgb": (16, 163, 127)},
    "grok": {"letter": "G", "short": "Grok", "rgb": (167, 139, 250)},
    "cursor": {"letter": "Cu", "short": "Cursor", "rgb": (96, 165, 250)},
    "gemini": {"letter": "Ge", "short": "Gemini", "rgb": (251, 191, 36)},
}

FOCUS_ORDER = ["grok", "codex", "claude", "cursor", "gemini", "openai"]


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


def _quota_of(p: ProviderReport) -> float | None:
    q = (p.meta or {}).get("quota") or {}
    pct = q.get("used_percent")
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
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _bar_color_for_pct(pct: float, base_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    if pct >= 90:
        return (255, 99, 99)
    if pct >= 70:
        return (245, 197, 66)
    return base_rgb


def _render_single_bar_icon(pct: float, rgb: tuple[int, int, int]) -> Path | None:
    """One horizontal usage bar for the menu bar (Grok-style)."""
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
    pt_w, pt_h = 26, 12
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

        # track
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.22, 0.26, 0.32, 1.0).set()
        track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(pad, y, track_w, bar_h), bar_h / 2, bar_h / 2
        )
        track.fill()

        fill_rgb = _bar_color_for_pct(pct, rgb)
        r, g, b = fill_rgb[0] / 255.0, fill_rgb[1] / 255.0, fill_rgb[2] / 255.0
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
        if pct > 0:
            fill_w = max(bar_h * 0.9, track_w * (pct / 100.0))
            fill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad, y, min(fill_w, track_w), bar_h), bar_h / 2, bar_h / 2
            )
            fill.fill()
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
    }

    app = rumps.App("llm-usage", title="…", quit_button=None)

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
            app_obj.title = " AI"
            try:
                app_obj.icon = None
            except Exception:
                pass
            return

        path = _render_single_bar_icon(pct, rgb)
        if path and path.exists():
            try:
                app_obj.template = False
                app_obj.icon = str(path)
            except Exception:
                pass
        # Colorful-feeling title: letter + percent (bar is the icon)
        app_obj.title = f" {style['letter']}{int(round(pct))}%"

    def rebuild_menu(report: AggregateReport | None, error: str | None = None) -> None:
        app.menu.clear()
        callbacks.clear()

        def add_enabled(title: str, callback=None, checked: bool = False) -> rumps.MenuItem:
            item = rumps.MenuItem(title)
            cb = callback or noop
            item.set_callback(cb)
            if checked:
                item.state = 1
            app.menu.add(item)
            callbacks.append(item)
            return item

        if error:
            add_enabled(f"⚠ {error[:70]}")

        if report is None:
            add_enabled("Loading…")
        else:
            add_enabled(
                f"Updated {datetime.now().strftime('%H:%M:%S')}  ·  "
                f"{report.period_start} → {report.period_end}"
            )
            app.menu.add(None)

            # ── Per-provider usage (enabled = not gray) ──
            for p in report.providers:
                pct = _quota_of(p)
                style = PROVIDER_STYLE.get(
                    p.provider.value, {"letter": "?", "rgb": (120, 140, 160)}
                )
                if pct is not None:
                    bar = _unicode_bar(pct, 12)
                    q = (p.meta or {}).get("quota") or {}
                    plan = q.get("plan") or ""
                    label = q.get("label") or "quota"
                    line = f"{style['letter']}  {p.display_name}  {bar}  {pct:.0f}%"
                    if plan:
                        line += f"  ·  {plan}"
                    item = add_enabled(line)
                    # Sub-windows
                    for w in q.get("windows") or []:
                        if w.get("used_percent") is None:
                            continue
                        wp = float(w["used_percent"])
                        sub = rumps.MenuItem(
                            f"    {w.get('label')}  {_unicode_bar(wp, 10)}  {wp:.0f}%"
                        )
                        sub.set_callback(noop)
                        item.add(sub)
                        callbacks.append(sub)
                    reset = q.get("resets_at")
                    if reset:
                        try:
                            d = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
                            sub = rumps.MenuItem(f"    Resets {d.strftime('%b %d, %H:%M')}")
                            sub.set_callback(noop)
                            item.add(sub)
                            callbacks.append(sub)
                        except ValueError:
                            pass
                elif p.requests or p.total_tokens:
                    cost = f"  ·  ${p.cost_usd:.2f}" if p.cost_usd is not None else ""
                    add_enabled(
                        f"{style['letter']}  {p.display_name}  ·  "
                        f"{p.requests:,} req  ·  {p.total_tokens:,} tok{cost}"
                    )
                else:
                    add_enabled(f"{style['letter']}  {p.display_name}  ·  not configured")

            app.menu.add(None)

            # ── Switch which bar shows in the menu bar ──
            focus_menu = rumps.MenuItem("Show in menu bar")
            focus_menu.set_callback(noop)
            callbacks.append(focus_menu)

            for pid in FOCUS_ORDER:
                # Only list providers we actually have a card for
                prov = _find_provider(report, pid)
                if prov is None:
                    continue
                style = PROVIDER_STYLE.get(pid, {"short": pid, "letter": "?"})
                pct = _quota_of(prov)
                label = style["short"]
                if pct is not None:
                    label = f"{style['short']}  ({pct:.0f}%)"
                elif prov.source.value == "unavailable" and not (
                    prov.requests or prov.total_tokens
                ):
                    label = f"{style['short']}  (n/a)"

                def _make_cb(provider_id: str):
                    def _cb(_=None, _pid=provider_id) -> None:
                        set_focus(_pid)

                    return _cb

                sub = rumps.MenuItem(label)
                sub.set_callback(_make_cb(pid))
                if state["focus"] == pid or (
                    state["focus"] == "openai" and pid == "codex"
                ):
                    sub.state = 1
                # also check exact focus match
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
                    _authenticated_dashboard_url()
                    or f"http://{settings.host}:{settings.port}/"
                )

            threading.Thread(target=_run, daemon=True).start()

        def _refresh(_=None) -> None:
            app.title = " …"
            threading.Thread(target=do_collect, daemon=True).start()

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

    def do_collect() -> None:
        if state["updating"]:
            return
        state["updating"] = True
        try:
            report = collect_all(settings, days=settings.days)
            state["report"] = report
            state["error"] = None
            _apply_status(app, report, state["focus"])
            rebuild_menu(report)
        except Exception as exc:  # noqa: BLE001
            state["error"] = str(exc)
            app.title = " AI!"
            rebuild_menu(state.get("report"), error=str(exc))
        finally:
            state["updating"] = False

    def background_loop() -> None:
        while True:
            do_collect()
            time.sleep(REFRESH_SECONDS)

    rebuild_menu(None)
    threading.Thread(target=background_loop, daemon=True).start()
    app.run()
