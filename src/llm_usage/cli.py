"""CLI entrypoint: `llm-usage`."""

from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from llm_usage import __version__
from llm_usage.config import load_settings
from llm_usage.history import daily_totals, sparkline, week_over_week_pct, weekly_buckets
from llm_usage.models import AggregateReport, ProviderReport, SourceKind
from llm_usage.providers import collect_all_cached
from llm_usage.quota import quota_windows
from llm_usage.serialize import report_to_dict

app = typer.Typer(
    name="llm-usage",
    help="See all your LLM usage in one place (Claude, OpenAI, Grok, Cursor, Gemini).",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


class OutputFormat(str, Enum):
    table = "table"
    json = "json"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    days: Optional[int] = typer.Option(
        None, "--days", "-d", help="Lookback window in days (default: 30)"
    ),
    fmt: OutputFormat = typer.Option(
        OutputFormat.table, "--format", "-f", help="Output format"
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter: claude, openai, codex, grok, cursor, gemini, openrouter",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Bypass the shared snapshot cache and force a live collection.",
    ),
    version: bool = typer.Option(False, "--version", help="Show version and exit"),
) -> None:
    """Show a unified usage summary (default command)."""
    if version:
        console.print(f"llm-usage {__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is not None:
        return
    _show(days=days, fmt=fmt, provider=provider, fresh=fresh)


@app.command("show")
def show_cmd(
    days: Optional[int] = typer.Option(None, "--days", "-d"),
    fmt: OutputFormat = typer.Option(OutputFormat.table, "--format", "-f"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Bypass the shared snapshot cache and force a live collection.",
    ),
) -> None:
    """Collect and display usage for all configured providers."""
    _show(days=days, fmt=fmt, provider=provider, fresh=fresh)


@app.command("dashboard")
def dashboard_cmd(
    port: Optional[int] = typer.Option(None, "--port", help="HTTP port"),
    host: Optional[str] = typer.Option(None, "--host", help="Bind host"),
    days: Optional[int] = typer.Option(None, "--days", "-d"),
) -> None:
    """Start a local web dashboard (http://127.0.0.1:8765)."""
    settings = load_settings()
    if days is not None:
        settings = settings.model_copy(update={"days": days})
    bind_host = host or settings.host
    bind_port = port or settings.port
    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[yellow]⚠ Binding to {bind_host}: keep this on a trusted network — "
            "the dashboard is only protected by a per-session token, not real "
            "authentication.[/yellow]"
        )

    from llm_usage.dashboard.app import create_app

    dashboard_app = create_app(settings)
    url = f"http://{bind_host}:{bind_port}/?token={dashboard_app.state.token}"

    console.print(
        Panel.fit(
            f"[bold]llm-usage dashboard[/bold]\n"
            f"Open [link={url}]{url}[/link]\n"
            "(the token is required — it's regenerated each run and never written to disk)\n"
            "Press Ctrl+C to stop.",
            border_style="cyan",
        )
    )
    import uvicorn

    uvicorn.run(
        dashboard_app,
        host=bind_host,
        port=bind_port,
        log_level="info",
    )


@app.command("menubar")
def menubar_cmd() -> None:
    """macOS menu bar: show Claude/Grok/Codex quota % in the top-right (click for details)."""
    from llm_usage.menubar import run_menubar

    console.print(
        "[cyan]Starting menu bar…[/cyan] Look for [bold]C## · G## · X##[/bold] "
        "near the clock. Click it for per-app usage."
    )
    run_menubar()


@app.command("status")
def status_cmd() -> None:
    """Show which credentials / local data sources are available."""
    settings = load_settings()
    table = Table(title="llm-usage data sources", box=box.ROUNDED)
    table.add_column("Provider", style="bold")
    table.add_column("Source")
    table.add_column("Status")

    rows = [
        (
            "Claude",
            "Admin API",
            "ready" if settings.anthropic_admin_key else "—",
        ),
        (
            "Claude",
            f"Local logs ({settings.claude_projects_dir})",
            "found" if settings.claude_projects_dir.exists() else "missing",
        ),
        (
            "Claude",
            "OAuth credentials",
            "found" if settings.claude_credentials_path.exists() else "missing",
        ),
        (
            "OpenAI",
            "Admin / API key",
            "ready"
            if (settings.openai_admin_key or settings.openai_api_key)
            else "—",
        ),
        (
            "Codex",
            f"Local sessions ({settings.codex_home_dir / 'sessions'})",
            "found" if (settings.codex_home_dir / "sessions").exists() else "missing",
        ),
        (
            "Codex",
            "ChatGPT OAuth (auth.json)",
            "found" if (settings.codex_home_dir / "auth.json").exists() else "missing",
        ),
        (
            "Grok Build",
            f"Local logs ({settings.grok_home_dir})",
            "found" if settings.grok_home_dir.exists() else "missing",
        ),
        (
            "Grok / xAI",
            "API key",
            "ready" if settings.xai_api_key else "—",
        ),
        (
            "Grok / xAI",
            "Management key + team",
            "ready"
            if (settings.xai_management_key and settings.xai_team_id)
            else "—",
        ),
        (
            "Cursor",
            "Admin API key",
            "ready" if settings.cursor_api_key else "—",
        ),
        (
            "Cursor",
            "Session token",
            "ready" if settings.cursor_session_token else "—",
        ),
        (
            "Gemini",
            "API key",
            "ready" if settings.gemini_api_key else "—",
        ),
        (
            "Gemini",
            f"Local CLI logs ({settings.gemini_home_dir})",
            "found" if settings.gemini_home_dir.exists() else "missing",
        ),
        (
            "OpenRouter",
            "API key",
            "ready" if settings.openrouter_api_key else "—",
        ),
    ]
    for provider, source, status in rows:
        style = (
            "green"
            if status in ("ready", "found")
            else ("dim" if status == "—" else "yellow")
        )
        table.add_row(provider, source, Text(status, style=style))

    console.print(table)
    console.print(
        "\n[dim]Copy .env.example → .env (or ~/.config/llm-usage/.env) and add keys.[/dim]"
    )


@app.command("doctor")
def doctor_cmd() -> None:
    """Live-check every configured source and explain what's wrong, if anything.

    Unlike `status` (which only checks that credential files/dirs exist),
    this runs a real collection — hitting live provider APIs — and reports
    per-provider health from the same errors/notes the normal collectors
    already produce.
    """
    settings = load_settings()
    with console.status("Running diagnostics (live checks, bypassing cache)…"):
        report = collect_all_cached(settings, days=7, force_refresh=True)

    table = Table(title="llm-usage doctor", box=box.ROUNDED)
    table.add_column("Provider", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    healthy = True
    for p in report.providers:
        configured = p.source != SourceKind.UNAVAILABLE or bool(p.errors)
        if not configured:
            status = Text("not configured", style="dim")
        elif p.errors and p.source == SourceKind.UNAVAILABLE:
            status = Text("error", style="red")
            healthy = False
        elif p.errors:
            status = Text("partial", style="yellow")
            healthy = False
        else:
            status = Text("ok", style="green")

        details = []
        if p.errors:
            details.extend(rich_escape(e) for e in p.errors[:3])
        elif p.notes:
            details.append(rich_escape(p.notes[0][:140]))
        table.add_row(p.display_name, status, "\n".join(details) or "—")

    console.print(table)
    if healthy:
        console.print("[green]All configured sources look healthy.[/green]")
    else:
        console.print(
            "[yellow]Some sources need attention — see Details above.[/yellow]"
        )
        raise typer.Exit(1)


@app.command("check")
def check_cmd(
    fail_at: float = typer.Option(
        90.0,
        "--fail-at",
        help="Exit 1 if any tracked quota window is at or above this percent.",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Bypass the shared snapshot cache and force a live collection.",
    ),
) -> None:
    """Scriptable quota check for cron/CI: non-zero exit if any provider's
    quota window is at or above --fail-at (default 90%)."""
    settings = load_settings()
    with console.status("Checking quotas…"):
        report = collect_all_cached(settings, days=7, force_refresh=fresh)

    table = Table(box=box.SIMPLE, show_edge=False)
    table.add_column("Provider")
    table.add_column("Window")
    table.add_column("Used", justify="right")

    breached: list[tuple[str, str, float]] = []
    for p in report.providers:
        for label, pct in quota_windows(p):
            style = "red" if pct >= fail_at else ("yellow" if pct >= fail_at * 0.75 else "")
            table.add_row(
                p.display_name, label, f"[{style}]{pct:.0f}%[/{style}]" if style else f"{pct:.0f}%"
            )
            if pct >= fail_at:
                breached.append((p.display_name, label, pct))

    console.print(table)
    if breached:
        console.print(f"\n[red]⚠ {len(breached)} window(s) at/above {fail_at:.0f}%:[/red]")
        for name, label, pct in breached:
            console.print(f"  · {name} — {label}: {pct:.0f}%")
        raise typer.Exit(1)
    console.print(f"\n[green]All tracked quota windows below {fail_at:.0f}%.[/green]")


@app.command("export")
def export_cmd(
    output: Path = typer.Option(Path("usage-report.json"), "--output", "-o"),
    days: Optional[int] = typer.Option(None, "--days", "-d"),
    include_raw: bool = typer.Option(
        False,
        "--include-raw",
        help="Include raw upstream payloads (OAuth usage bodies, billing "
        "snapshots, API-key listings) in the export. Off by default since "
        "these can be sensitive.",
    ),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Bypass the shared snapshot cache and force a live collection.",
    ),
) -> None:
    """Write a JSON usage report to disk."""
    import json
    import os

    settings = load_settings()
    with console.status("Collecting usage…"):
        report = collect_all_cached(settings, days=days, force_refresh=fresh)
    data = report_to_dict(report, include_raw_meta=include_raw)
    output.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    console.print(f"[green]Wrote[/green] {output.resolve()}")


@app.command("history")
def history_cmd(
    weeks: int = typer.Option(8, "--weeks", "-w", help="Number of weeks to show"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Bypass the shared snapshot cache and force a live collection.",
    ),
) -> None:
    """Show daily/weekly usage trends per provider."""
    settings = load_settings()
    days = max(weeks, 1) * 7
    with console.status("Collecting usage history…"):
        report = collect_all_cached(settings, days=days, force_refresh=fresh)

    providers = report.providers
    if provider:
        want = provider.lower().strip()
        providers = [
            p for p in providers if p.provider.value == want or want in p.display_name.lower()
        ]
        if not providers:
            console.print(f"[red]No provider matching[/red] {provider!r}")
            raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[bold]Usage history[/bold]  "
            f"{report.period_start.isoformat()} → {report.period_end.isoformat()}",
            border_style="cyan",
        )
    )

    shown_any = False
    for p in providers:
        if not p.daily:
            continue
        shown_any = True
        _print_provider_history(p, report.period_start, report.period_end)

    if not shown_any:
        console.print(
            "[dim]No daily history available yet for the selected provider(s). "
            "Local-log providers (Claude, Codex, Grok, Gemini) build this up as "
            "you use them; API-backed providers need a longer --days window.[/dim]"
        )


def _print_provider_history(p: ProviderReport, start: date, end: date) -> None:
    totals = daily_totals(p.daily, start, end)
    spark = sparkline(totals)

    console.print(f"\n[bold]{p.display_name}[/bold]  {spark}")

    buckets = weekly_buckets(p.daily)
    if not buckets:
        return

    table = Table(box=box.SIMPLE, show_edge=False, pad_edge=False)
    table.add_column("Week of")
    table.add_column("Tokens", justify="right")
    table.add_column("Requests", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("vs prior wk", justify="right")

    prev_tokens: float | None = None
    for b in buckets:
        trend = ""
        if prev_tokens is not None:
            pct = week_over_week_pct(b.total_tokens, prev_tokens)
            if pct is not None:
                arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "→")
                color = "red" if pct > 0 else ("green" if pct < 0 else "dim")
                trend = f"[{color}]{arrow} {abs(pct):.0f}%[/{color}]"
        table.add_row(
            b.week_start.isoformat(),
            f"{b.total_tokens:,}",
            f"{b.requests:,}",
            f"${b.cost_usd:,.2f}" if b.cost_usd is not None else "—",
            trend,
        )
        prev_tokens = b.total_tokens

    console.print(table)


def _show(
    days: Optional[int],
    fmt: OutputFormat,
    provider: Optional[str],
    fresh: bool = False,
) -> None:
    settings = load_settings()
    with console.status("Collecting usage from providers…"):
        report = collect_all_cached(settings, days=days, force_refresh=fresh)

    if provider:
        want = provider.lower().strip()
        report.providers = [
            p
            for p in report.providers
            if p.provider.value == want or want in p.display_name.lower()
        ]
        if not report.providers:
            console.print(f"[red]No provider matching[/red] {provider!r}")
            raise typer.Exit(1)

    if fmt == OutputFormat.json:
        import json

        console.print_json(json.dumps(report_to_dict(report)))
        return

    _print_table(report)


def _print_table(report: AggregateReport) -> None:
    header = (
        f"[bold]LLM Usage[/bold]  "
        f"{report.period_start.isoformat()} → {report.period_end.isoformat()}"
    )
    console.print(Panel.fit(header, border_style="cyan"))

    table = Table(box=box.SIMPLE_HEAVY, show_footer=True)
    table.add_column("Provider", style="bold", footer="TOTAL")
    table.add_column("Source", footer="")
    table.add_column("Requests", justify="right", footer=f"{report.total_requests:,}")
    table.add_column("Input tok", justify="right", footer="")
    table.add_column("Output tok", justify="right", footer="")
    table.add_column("Total tok", justify="right", footer=f"{report.total_tokens:,}")
    cost_footer = (
        f"${report.total_cost_usd:,.2f}" if report.total_cost_usd is not None else "—"
    )
    table.add_column("Cost (USD)", justify="right", footer=cost_footer)
    table.add_column("Notes")

    for p in report.providers:
        table.add_row(
            p.display_name,
            _source_label(p.source),
            f"{p.requests:,}" if p.requests else "—",
            f"{p.input_tokens:,}" if p.input_tokens else "—",
            f"{p.output_tokens:,}" if p.output_tokens else "—",
            f"{p.total_tokens:,}" if p.total_tokens else "—",
            _cost_label(p),
            _notes_short(p),
        )

    console.print(table)

    # Per-provider model breakdown when we have data
    for p in report.providers:
        if not p.models:
            continue
        mt = Table(
            title=f"{p.display_name} · models",
            box=box.MINIMAL_DOUBLE_HEAD,
            show_lines=False,
        )
        mt.add_column("Model")
        mt.add_column("Requests", justify="right")
        mt.add_column("In", justify="right")
        mt.add_column("Out", justify="right")
        mt.add_column("Cache R/W", justify="right")
        mt.add_column("Est. $", justify="right")
        for m in p.models[:15]:
            mt.add_row(
                m.model,
                f"{m.requests:,}",
                f"{m.input_tokens:,}",
                f"{m.output_tokens:,}",
                f"{m.cache_read_tokens:,}/{m.cache_write_tokens:,}",
                f"${m.cost_usd:,.4f}" if m.cost_usd is not None else "—",
            )
        console.print(mt)

    # Errors / hints
    for p in report.providers:
        for err in p.errors:
            console.print(f"[yellow]⚠ {p.display_name}:[/yellow] {err}")
        for note in p.notes:
            # Always show quota/plan notes; only show setup hints when empty
            is_quota = any(
                k in note.lower()
                for k in ("quota", "plan=", "plan type", "live ", "weekly", "x premium")
            )
            if is_quota or (p.total_tokens == 0 and p.cost_usd is None):
                style = "cyan" if is_quota else "dim"
                console.print(f"[{style}]· {p.display_name}: {note}[/{style}]")

    if report.total_cost_usd is not None:
        console.print(
            f"\n[bold green]Combined cost (where known):[/bold green] "
            f"${report.total_cost_usd:,.2f}"
        )
    console.print(
        "[dim]Tip: llm-usage dashboard  ·  llm-usage status  ·  llm-usage --format json[/dim]"
    )


def _source_label(source: SourceKind) -> str:
    colors = {
        SourceKind.API: "green",
        SourceKind.LOCAL_LOGS: "cyan",
        SourceKind.SUBSCRIPTION: "magenta",
        SourceKind.MANUAL: "blue",
        SourceKind.UNAVAILABLE: "dim",
    }
    return f"[{colors.get(source, 'white')}]{source.value}[/{colors.get(source, 'white')}]"


def _cost_label(p: ProviderReport) -> str:
    if p.cost_usd is None:
        return "—"
    suffix = " ~" if p.meta.get("estimated") else ""
    return f"${p.cost_usd:,.2f}{suffix}"


def _notes_short(p: ProviderReport) -> str:
    if p.source == SourceKind.UNAVAILABLE:
        return "not configured"
    if p.meta.get("estimated"):
        return "estimated" + (" · warn" if p.errors else "")
    if p.meta.get("subscription"):
        return "incl. quota"
    if p.errors and p.total_tokens == 0 and p.cost_usd is None:
        return "error"
    if p.errors:
        return "partial"
    return ""


if __name__ == "__main__":
    app()
