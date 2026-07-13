"""macOS menu bar app — show AI quota % in the corner (like Kanary)."""

from __future__ import annotations

import subprocess
import threading
import time
import webbrowser
from datetime import datetime

from llm_usage.config import load_settings
from llm_usage.models import AggregateReport, ProviderReport
from llm_usage.providers import collect_all


REFRESH_SECONDS = 120  # poll interval


def _quota_of(p: ProviderReport) -> float | None:
    q = (p.meta or {}).get("quota") or {}
    pct = q.get("used_percent")
    if pct is None:
        return None
    try:
        return float(pct)
    except (TypeError, ValueError):
        return None


def _title_from_report(report: AggregateReport) -> str:
    """Compact menu-bar title, e.g. 'G63 · C41 · X29'."""
    parts: list[str] = []
    letters = {
        "claude": "C",
        "codex": "X",
        "openai": "O",
        "grok": "G",
        "cursor": "Cu",
        "gemini": "Ge",
    }
    for p in report.providers:
        pct = _quota_of(p)
        if pct is None:
            continue
        letter = letters.get(p.provider.value, p.provider.value[:1].upper())
        parts.append(f"{letter}{int(round(pct))}")
    if not parts:
        # Fall back to highest activity signal
        active = [p for p in report.providers if p.source.value != "unavailable"]
        if not active:
            return "AI · —"
        return f"AI · {len(active)}"
    # Prefer showing max used as primary number if space is tight
    if len(parts) == 1:
        return f"{parts[0]}%"
    return " · ".join(parts)


def _menu_line(p: ProviderReport) -> str:
    pct = _quota_of(p)
    q = (p.meta or {}).get("quota") or {}
    plan = q.get("plan") or ""
    if pct is not None:
        label = q.get("label") or "quota"
        reset = q.get("resets_at")
        extra = f" · {plan}" if plan else ""
        line = f"{p.display_name}: {pct:.0f}% ({label}){extra}"
        if reset:
            try:
                d = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
                line += f" · resets {d.strftime('%b %d %H:%M')}"
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

    app = rumps.App("llm-usage", title="AI · …", quit_button=None)

    # Placeholder items (rebuilt on refresh)
    status_item = rumps.MenuItem("Loading…")
    open_dash = rumps.MenuItem("Open Dashboard")
    refresh_item = rumps.MenuItem("Refresh Now")
    quit_item = rumps.MenuItem("Quit llm-usage")

    def rebuild_menu(report: AggregateReport | None, error: str | None = None) -> None:
        app.menu.clear()
        if error:
            app.menu.add(rumps.MenuItem(f"Error: {error[:60]}"))
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
            for p in report.providers:
                # Clicking a provider line is informational (no-op callback)
                item = rumps.MenuItem(_menu_line(p))
                # Sub-windows for Claude etc.
                windows = ((p.meta or {}).get("quota") or {}).get("windows") or []
                for w in windows:
                    if w.get("used_percent") is None:
                        continue
                    sub = rumps.MenuItem(
                        f"  {w.get('label')}: {float(w['used_percent']):.0f}%"
                    )
                    item.add(sub)
                app.menu.add(item)
            app.menu.add(None)
            # Quick summary
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
            app.title = _title_from_report(report)
            rebuild_menu(report)
        except Exception as exc:  # noqa: BLE001
            state["error"] = str(exc)
            app.title = "AI · !"
            rebuild_menu(state.get("report"), error=str(exc))
        finally:
            state["updating"] = False

    def background_loop() -> None:
        while True:
            do_collect()
            time.sleep(REFRESH_SECONDS)

    @open_dash.set_callback
    def _open_dashboard(_=None) -> None:
        # Start dashboard if needed, then open browser
        def _run() -> None:
            try:
                # Try open existing
                import httpx

                r = httpx.get(
                    f"http://{settings.host}:{settings.port}/api/health", timeout=1.0
                )
                if r.status_code == 200:
                    webbrowser.open(f"http://{settings.host}:{settings.port}/")
                    return
            except Exception:  # noqa: BLE001
                pass
            # Launch dashboard in background
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
        app.title = "AI · …"
        threading.Thread(target=do_collect, daemon=True).start()

    @quit_item.set_callback
    def _quit(_=None) -> None:
        rumps.quit_application()

    rebuild_menu(None)
    threading.Thread(target=background_loop, daemon=True).start()
    app.run()
