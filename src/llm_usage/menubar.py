"""macOS menu bar app — visual usage bars in the top-right corner."""

from __future__ import annotations

import math
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


REFRESH_SECONDS = 120

# Display order + colors (RGB 0–255) for bars
PROVIDER_STYLE: dict[str, dict] = {
    "claude": {"letter": "C", "rgb": (212, 162, 127)},
    "codex": {"letter": "X", "rgb": (34, 197, 94)},
    "openai": {"letter": "O", "rgb": (16, 163, 127)},
    "grok": {"letter": "G", "rgb": (167, 139, 250)},
    "cursor": {"letter": "Cu", "rgb": (96, 165, 250)},
    "gemini": {"letter": "Ge", "rgb": (251, 191, 36)},
}


def _quota_of(p: ProviderReport) -> float | None:
    q = (p.meta or {}).get("quota") or {}
    pct = q.get("used_percent")
    if pct is None:
        return None
    try:
        return max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return None


def _active_quotas(report: AggregateReport) -> list[tuple[str, str, float, tuple[int, int, int]]]:
    """Return (provider_id, letter, pct, rgb) for providers with quota %."""
    out: list[tuple[str, str, float, tuple[int, int, int]]] = []
    for p in report.providers:
        pct = _quota_of(p)
        if pct is None:
            continue
        style = PROVIDER_STYLE.get(p.provider.value, {"letter": "?", "rgb": (120, 140, 160)})
        out.append((p.provider.value, style["letter"], pct, style["rgb"]))
    return out


def _unicode_bar(pct: float, width: int = 10) -> str:
    """Text progress bar using block characters."""
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    # Prefer solid blocks that render well in menu bar fonts
    return "█" * filled + "░" * (width - filled)


def _title_from_report(report: AggregateReport) -> str:
    """
    Menu bar title with mini usage bars, e.g.:
      X ▓▓▓░░░░░ 29  G ████████░ 84
    Falls back to highest-only if many providers.
    """
    quotas = _active_quotas(report)
    if not quotas:
        active = [p for p in report.providers if p.source.value != "unavailable"]
        return "AI —" if not active else f"AI {len(active)}"

    # 1–2 providers: full bars; 3+: letter + short bar + %
    if len(quotas) <= 2:
        parts = []
        for _pid, letter, pct, _rgb in quotas:
            parts.append(f"{letter} {_unicode_bar(pct, 8)} {int(round(pct))}")
        return "  ".join(parts)

    # Many: short bars
    parts = []
    for _pid, letter, pct, _rgb in quotas[:4]:
        parts.append(f"{letter}{_unicode_bar(pct, 5)}{int(round(pct))}")
    return " ".join(parts)


def _menu_line(p: ProviderReport) -> str:
    pct = _quota_of(p)
    q = (p.meta or {}).get("quota") or {}
    plan = q.get("plan") or ""
    if pct is not None:
        bar = _unicode_bar(pct, 12)
        label = q.get("label") or "quota"
        extra = f" · {plan}" if plan else ""
        line = f"{p.display_name}  {bar}  {pct:.0f}%  ({label}){extra}"
        reset = q.get("resets_at")
        if reset:
            try:
                d = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
                line += f" · ↺ {d.strftime('%b %d %H:%M')}"
            except ValueError:
                pass
        return line
    if p.requests or p.total_tokens:
        return (
            f"{p.display_name}: {p.requests:,} req · "
            f"{p.total_tokens:,} tok"
            + (f" · ${p.cost_usd:.2f}" if p.cost_usd is not None else "")
        )
    if p.source.value == "unavailable":
        return f"{p.display_name}: not configured"
    return f"{p.display_name}: {p.source.value}"


def _render_bar_icon_png(quotas: list[tuple[str, str, float, tuple[int, int, int]]]) -> Path | None:
    """
    Draw a compact multi-row usage-bar icon for the menu bar.

    Returns path to a temporary PNG (caller may leave it; OS cleans temp).
    """
    try:
        from AppKit import (  # type: ignore
            NSBitmapImageRep,
            NSCalibratedRGBColorSpace,
            NSDeviceRGBColorSpace,
            NSGraphicsContext,
            NSImage,
            NSPNGFileType,
        )
        from Foundation import NSMakeRect  # type: ignore
    except ImportError:
        return None

    # Retina-friendly pixel size (points × 2)
    scale = 2
    # width in points ~ 22–28 so it sits nicely in the menu bar
    pt_w, pt_h = 28, 18
    px_w, px_h = pt_w * scale, pt_h * scale

    if not quotas:
        # empty grey bar
        quotas = [("none", "?", 0.0, (90, 100, 110))]

    # Cap to 3 rows so icon stays readable
    rows = quotas[:3]
    n = len(rows)

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None,
        px_w,
        px_h,
        8,
        4,
        True,
        False,
        NSCalibratedRGBColorSpace,
        0,
        0,
    )
    if rep is None:
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None,
            px_w,
            px_h,
            8,
            4,
            True,
            False,
            NSDeviceRGBColorSpace,
            0,
            0,
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
            ctx.setImageInterpolation_(3)  # high

        # Clear transparent background
        from AppKit import NSBezierPath, NSColor  # type: ignore

        NSColor.clearColor().set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, pt_w, pt_h))

        pad_x = 1.0
        pad_y = 1.5
        gap = 1.5
        usable_h = pt_h - 2 * pad_y - gap * (n - 1)
        bar_h = max(2.5, usable_h / n)
        track_w = pt_w - 2 * pad_x

        for i, (_pid, _letter, pct, rgb) in enumerate(rows):
            y = pt_h - pad_y - (i + 1) * bar_h - i * gap
            # track (dark)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.22, 0.28, 0.95).set()
            track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad_x, y, track_w, bar_h), bar_h / 2, bar_h / 2
            )
            track.fill()
            # fill
            r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
            # warn tint when high
            if pct >= 90:
                r, g, b = 1.0, 0.42, 0.42
            elif pct >= 70:
                r, g, b = 0.96, 0.77, 0.26
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
            fill_w = max(bar_h, track_w * (pct / 100.0))  # at least a pill tip
            if pct <= 0:
                continue
            fill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(pad_x, y, min(fill_w, track_w), bar_h), bar_h / 2, bar_h / 2
            )
            fill.fill()
    finally:
        img.unlockFocus()

    # Write PNG
    tdir = Path(tempfile.gettempdir()) / "llm-usage-menubar"
    tdir.mkdir(exist_ok=True)
    out = tdir / "status.png"
    try:
        # Prefer representation that has pixels
        tiff = img.TIFFRepresentation()
        if tiff is None:
            return None
        from AppKit import NSBitmapImageRep as BIR  # type: ignore

        rep2 = BIR.imageRepWithData_(tiff)
        if rep2 is None:
            return None
        data = rep2.representationUsingType_properties_(NSPNGFileType, None)
        if data is None:
            return None
        data.writeToFile_atomically_(str(out), True)
        return out
    except Exception:
        return None


def _apply_icon(app, report: AggregateReport | None) -> None:
    """Set menu bar icon to usage bars; keep a short title as backup."""
    if report is None:
        app.title = "AI …"
        try:
            app.icon = None
        except Exception:
            pass
        return

    quotas = _active_quotas(report)
    path = _render_bar_icon_png(quotas)
    title = _title_from_report(report)

    if path and path.exists():
        try:
            # Color bars — not a template (monochrome) image
            app.template = False
            app.icon = str(path)
            # Short title next to icon: highest usage only (avoids clutter)
            if quotas:
                # Show max % next to the bar graphic
                max_pct = max(q[2] for q in quotas)
                letter = next(q[1] for q in quotas if q[2] == max_pct)
                app.title = f" {int(round(max_pct))}%"
            else:
                app.title = ""
            return
        except Exception:
            pass

    # Fallback: text-only unicode bars (no AppKit image)
    app.title = title
    try:
        app.icon = None
    except Exception:
        pass


def run_menubar() -> None:
    """Start the menu bar app (blocks). Requires rumps on macOS."""
    try:
        import rumps  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Menu bar requires 'rumps'. Install with:\n"
            "  uv tool install --force -e ~/personal/tool/llm-usage\n"
            "or: pip install rumps\n"
            f"({exc})"
        ) from exc

    settings = load_settings()
    state: dict = {"report": None, "error": None, "updating": False}

    app = rumps.App("llm-usage", title="AI …", quit_button=None)

    status_item = rumps.MenuItem("Loading…")
    open_dash = rumps.MenuItem("Open Dashboard")
    refresh_item = rumps.MenuItem("Refresh Now")
    quit_item = rumps.MenuItem("Quit llm-usage")

    def rebuild_menu(report: AggregateReport | None, error: str | None = None) -> None:
        app.menu.clear()
        if error:
            app.menu.add(rumps.MenuItem(f"⚠ {error[:70]}"))
        if report is None:
            app.menu.add(status_item)
        else:
            app.menu.add(
                rumps.MenuItem(
                    f"Updated {datetime.now().strftime('%H:%M:%S')} · "
                    f"{report.period_start} → {report.period_end}"
                )
            )
            app.menu.add(None)
            # Visual bar legend
            quotas = _active_quotas(report)
            if quotas:
                legend = "  ".join(f"{letter}={int(round(pct))}%" for _p, letter, pct, _c in quotas)
                app.menu.add(rumps.MenuItem(f"Bars: {legend}"))
                app.menu.add(None)
            for p in report.providers:
                item = rumps.MenuItem(_menu_line(p))
                windows = ((p.meta or {}).get("quota") or {}).get("windows") or []
                for w in windows:
                    if w.get("used_percent") is None:
                        continue
                    wp = float(w["used_percent"])
                    sub = rumps.MenuItem(
                        f"  {w.get('label')}  {_unicode_bar(wp, 10)}  {wp:.0f}%"
                    )
                    item.add(sub)
                app.menu.add(item)
            app.menu.add(None)
            costs = [p.cost_usd for p in report.providers if p.cost_usd is not None]
            if costs:
                app.menu.add(rumps.MenuItem(f"Known cost: ${sum(costs):.2f}"))
        app.menu.add(None)
        app.menu.add(open_dash)
        app.menu.add(refresh_item)
        app.menu.add(None)
        app.menu.add(quit_item)

    def do_collect() -> None:
        if state["updating"]:
            return
        state["updating"] = True
        try:
            report = collect_all(settings, days=settings.days)
            state["report"] = report
            state["error"] = None
            _apply_icon(app, report)
            rebuild_menu(report)
        except Exception as exc:  # noqa: BLE001
            state["error"] = str(exc)
            app.title = "AI !"
            rebuild_menu(state.get("report"), error=str(exc))
        finally:
            state["updating"] = False

    def background_loop() -> None:
        while True:
            do_collect()
            time.sleep(REFRESH_SECONDS)

    @open_dash.set_callback
    def _open_dashboard(_=None) -> None:
        def _run() -> None:
            try:
                import httpx

                r = httpx.get(
                    f"http://{settings.host}:{settings.port}/api/health", timeout=1.0
                )
                if r.status_code == 200:
                    webbrowser.open(f"http://{settings.host}:{settings.port}/")
                    return
            except Exception:  # noqa: BLE001
                pass
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
            time.sleep(1.2)
            webbrowser.open(f"http://{settings.host}:{settings.port}/")

        threading.Thread(target=_run, daemon=True).start()

    @refresh_item.set_callback
    def _refresh(_=None) -> None:
        app.title = "…"
        threading.Thread(target=do_collect, daemon=True).start()

    @quit_item.set_callback
    def _quit(_=None) -> None:
        rumps.quit_application()

    rebuild_menu(None)
    threading.Thread(target=background_loop, daemon=True).start()
    app.run()
