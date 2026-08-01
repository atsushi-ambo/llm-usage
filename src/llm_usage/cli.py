"""CLI entrypoint: `llm-usage`."""

from __future__ import annotations

import os
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from llm_usage import __version__
from llm_usage.config import (
    get_active_profile,
    get_profile_env_file,
    list_profiles,
    load_settings,
    set_active_profile,
)
from llm_usage.history import daily_totals, sparkline, week_over_week_pct, weekly_buckets
from llm_usage.models import AggregateReport, ProviderReport, SourceKind
from llm_usage.providers import collect_all_cached
from llm_usage.quota import quota_windows
from llm_usage.scheduler import (
    ScheduleFrequency,
    ScheduledReport,
    calculate_next_run,
    delete_schedule,
    list_schedules,
    load_schedule,
    run_scheduled_report,
    save_schedule,
)
from llm_usage.serialize import report_to_dict
from llm_usage.validation import format_validation_errors, validate_settings
from llm_usage.wizard import run_setup_wizard

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
    i_understand_no_auth: bool = typer.Option(
        False,
        "--i-understand-no-auth",
        help="Required to bind on a non-loopback host. The dashboard only has a "
        "per-session token — anyone who can reach the port and the token can "
        "read your usage data.",
    ),
) -> None:
    """Start a local web dashboard (http://127.0.0.1:8765)."""
    settings = load_settings()
    if days is not None:
        settings = settings.model_copy(update={"days": days})
    bind_host = host or settings.host
    bind_port = port or settings.port
    loopback = {"127.0.0.1", "localhost", "::1"}
    if bind_host not in loopback:
        if not i_understand_no_auth:
            console.print(
                f"[red]Refusing to bind to {bind_host}.[/red] Default is loopback only. "
                "If you really need a non-local bind, pass --i-understand-no-auth "
                "(token-only protection — not safe on untrusted networks)."
            )
            raise typer.Exit(code=2)
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
            "(token required; also stored 0600 under ~/.config/llm-usage/cache "
            "so the menubar can open an authenticated tab)\n"
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
        "[cyan]Starting menu bar…[/cyan] Look for [bold]C## · G## · O##[/bold] "
        "near the clock. Click it for per-app usage."
    )
    run_menubar()


@app.command("setup")
def setup_cmd(
    profile: Optional[str] = typer.Option(
        None, "--profile", help="Write into a named profile (.env.<name>)"
    ),
) -> None:
    """Run the interactive setup wizard for first-time configuration."""
    run_setup_wizard(profile=profile)


@app.command("validate")
def validate_cmd() -> None:
    """Validate configuration and show any errors or warnings."""
    settings = load_settings()
    errors = validate_settings(settings)
    console.print(format_validation_errors(errors))
    if any(e.severity == "error" for e in errors):
        raise typer.Exit(1)


@app.command("profile")
def profile_cmd(
    action: str = typer.Argument(..., help="Action: list, create, switch, delete, clear"),
    name: Optional[str] = typer.Argument(None, help="Profile name"),
) -> None:
    """Manage configuration profiles (work/personal configs)."""
    if action == "list":
        profiles = list_profiles()
        active = get_active_profile()
        if profiles:
            console.print("[bold]Available profiles:[/bold]")
            for profile in profiles:
                mark = " [cyan](active)[/cyan]" if profile == active else ""
                console.print(f"  • {profile}{mark}")
        else:
            console.print(
                "[dim]No profiles found. Create one with "
                "'llm-usage profile create <name>'[/dim]"
            )
        if active and active not in profiles:
            console.print(f"[yellow]Active profile '{active}' has no .env file yet[/yellow]")
        elif not active:
            console.print("[dim]Active: default (~/.config/llm-usage/.env)[/dim]")
    elif action == "create":
        if not name:
            console.print("[red]Error: Profile name required[/red]")
            raise typer.Exit(1)
        try:
            env_file = get_profile_env_file(name)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if env_file.exists():
            console.print(f"[yellow]Profile '{name}' already exists[/yellow]")
            raise typer.Exit(1)
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.touch()
        os.chmod(env_file, 0o600)
        console.print(f"[green]Created profile '{name}' at {env_file}[/green]")
        console.print(
            f"[dim]Edit it or run: llm-usage setup --profile {name}[/dim]"
        )
    elif action == "switch":
        if not name:
            console.print("[red]Error: Profile name required[/red]")
            raise typer.Exit(1)
        try:
            env_file = get_profile_env_file(name)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if not env_file.exists():
            console.print(f"[red]Profile '{name}' does not exist[/red]")
            raise typer.Exit(1)
        set_active_profile(name)
        console.print(f"[green]Switched to profile '{name}'[/green]")
        console.print(
            f"[dim]Persisted in ~/.config/llm-usage/active_profile "
            f"(override with LLM_USAGE_PROFILE={name})[/dim]"
        )
    elif action == "clear":
        set_active_profile(None)
        console.print("[green]Cleared active profile — using default .env[/green]")
    elif action == "delete":
        if not name:
            console.print("[red]Error: Profile name required[/red]")
            raise typer.Exit(1)
        try:
            env_file = get_profile_env_file(name)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if not env_file.exists():
            console.print(f"[red]Profile '{name}' does not exist[/red]")
            raise typer.Exit(1)
        if Confirm.ask(f"Delete profile '{name}'?"):
            env_file.unlink()
            if get_active_profile() == name:
                set_active_profile(None)
            console.print(f"[green]Deleted profile '{name}'[/green]")
    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print("Valid actions: list, create, switch, clear, delete")
        raise typer.Exit(1)


@app.command("schedule")
def schedule_cmd(
    action: str = typer.Argument(..., help="Action: list, create, run, delete"),
    name: Optional[str] = typer.Argument(None, help="Schedule name"),
    frequency: Optional[str] = typer.Option(
        None, "--frequency", "-f", help="Frequency: daily, weekly, monthly"
    ),
    days: int = typer.Option(30, "--days", "-d", help="Lookback period in days"),
    format: str = typer.Option(
        "csv", "--format", help="Export format: csv, json, txt"
    ),
) -> None:
    """Manage scheduled automated reports (exports under cache/schedules/exports)."""
    if action == "list":
        schedules = list_schedules()
        if schedules:
            table = Table(title="Scheduled Reports", box=box.ROUNDED)
            table.add_column("Name", style="bold")
            table.add_column("Frequency")
            table.add_column("Days")
            table.add_column("Format")
            table.add_column("Status")
            table.add_column("Next Run")
            for schedule in schedules:
                status = (
                    "[green]enabled[/green]"
                    if schedule.enabled
                    else "[red]disabled[/red]"
                )
                next_run = (
                    schedule.next_run.strftime("%Y-%m-%d %H:%M")
                    if schedule.next_run
                    else "—"
                )
                table.add_row(
                    schedule.name,
                    schedule.frequency.value,
                    str(schedule.days),
                    schedule.export_format,
                    status,
                    next_run,
                )
            console.print(table)
        else:
            console.print(
                "[dim]No scheduled reports. Create one with "
                "'llm-usage schedule create <name> --frequency daily'[/dim]"
            )
    elif action == "create":
        if not name:
            console.print("[red]Error: Schedule name required[/red]")
            raise typer.Exit(1)
        if not frequency:
            console.print(
                "[red]Error: --frequency required (daily, weekly, monthly)[/red]"
            )
            raise typer.Exit(1)
        try:
            freq = ScheduleFrequency(frequency.lower())
            schedule = ScheduledReport(
                name=name,
                frequency=freq,
                days=days,
                export_format=format,
            )
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        schedule.next_run = calculate_next_run(freq)
        save_schedule(schedule)
        console.print(f"[green]Created schedule '{name}'[/green]")
        console.print(
            f"[dim]Next run: {schedule.next_run.strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
        )
        console.print(
            "[dim]Run due jobs with: llm-usage schedule run <name> "
            "(or cron that call).[/dim]"
        )
    elif action == "run":
        if not name:
            console.print("[red]Error: Schedule name required[/red]")
            raise typer.Exit(1)
        schedule = load_schedule(name)
        if not schedule:
            console.print(f"[red]Schedule '{name}' not found[/red]")
            raise typer.Exit(1)
        console.print(f"[cyan]Running scheduled report '{name}'…[/cyan]")
        with console.status("Collecting usage…"):
            run_scheduled_report(schedule)
        schedule = load_schedule(name)
        console.print("[green]Report generated and exported[/green]")
        if schedule and schedule.next_run:
            console.print(
                f"[dim]Next run: "
                f"{schedule.next_run.strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
            )
    elif action == "delete":
        if not name:
            console.print("[red]Error: Schedule name required[/red]")
            raise typer.Exit(1)
        if delete_schedule(name):
            console.print(f"[green]Deleted schedule '{name}'[/green]")
        else:
            console.print(f"[red]Schedule '{name}' not found[/red]")
    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print("Valid actions: list, create, run, delete")
        raise typer.Exit(1)


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
        (
            "Cohere",
            "API key",
            "ready" if settings.cohere_api_key else "—",
        ),
        (
            "Mistral",
            "API key",
            "ready" if settings.mistral_api_key else "—",
        ),
        (
            "Replicate",
            "API key",
            "ready" if settings.replicate_api_key else "—",
        ),
        (
            "Hugging Face",
            "API key",
            "ready" if settings.huggingface_api_key else "—",
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
def doctor_cmd(
    fmt: OutputFormat = typer.Option(
        OutputFormat.table, "--format", "-f", help="Output format"
    ),
) -> None:
    """Live-check every configured source and explain what's wrong, if anything.

    Unlike `status` (which only checks that credential files/dirs exist),
    this runs a real collection — hitting live provider APIs — and reports
    per-provider health from the same errors/notes the normal collectors
    already produce.
    """
    settings = load_settings()
    with console.status("Running diagnostics (live checks, bypassing cache)…"):
        report = collect_all_cached(settings, days=7, force_refresh=True)

    rows: list[dict[str, object]] = []
    healthy = True
    for p in report.providers:
        configured = p.source != SourceKind.UNAVAILABLE or bool(p.errors)
        if not configured:
            status = "not_configured"
        elif p.errors and p.source == SourceKind.UNAVAILABLE:
            status = "error"
            healthy = False
        elif p.errors:
            status = "partial"
            healthy = False
        else:
            status = "ok"

        details: list[str] = []
        if p.errors:
            details.extend(p.errors[:3])
        elif p.notes:
            details.append(p.notes[0][:140])
        rows.append(
            {
                "provider": p.provider.value,
                "display_name": p.display_name,
                "status": status,
                "source": p.source.value,
                "errors": list(p.errors),
                "notes": list(p.notes[:3]),
                "details": details,
            }
        )

    if fmt == OutputFormat.json:
        import json

        console.print_json(
            json.dumps({"ok": healthy, "providers": rows})
        )
        if not healthy:
            raise typer.Exit(1)
        return

    table = Table(title="llm-usage doctor", box=box.ROUNDED)
    table.add_column("Provider", style="bold")
    table.add_column("Status")
    table.add_column("Details")
    status_style = {
        "not_configured": "dim",
        "error": "red",
        "partial": "yellow",
        "ok": "green",
    }
    for row in rows:
        st = str(row["status"])
        detail_items = row["details"]
        if isinstance(detail_items, list) and detail_items:
            detail_text = "\n".join(rich_escape(str(d)) for d in detail_items)
        else:
            detail_text = "—"
        table.add_row(
            str(row["display_name"]),
            Text(st.replace("_", " "), style=status_style.get(st, "")),
            detail_text,
        )

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
    fmt: OutputFormat = typer.Option(
        OutputFormat.table, "--format", "-f", help="Output format"
    ),
) -> None:
    """Scriptable quota check for cron/CI: non-zero exit if any provider's
    quota window is at or above --fail-at (default 90%)."""
    settings = load_settings()
    with console.status("Checking quotas…"):
        # Quotas only — no local log scans (cron shouldn't re-parse months
        # of Claude/Codex transcripts just to print % used).
        report = collect_all_cached(
            settings, days=7, force_refresh=fresh, quota_only=True
        )

    windows: list[dict[str, object]] = []
    breached: list[dict[str, object]] = []
    for p in report.providers:
        for label, pct in quota_windows(p):
            entry = {
                "provider": p.provider.value,
                "display_name": p.display_name,
                "window": label,
                "used_percent": pct,
            }
            windows.append(entry)
            if pct >= fail_at:
                breached.append(entry)

    if fmt == OutputFormat.json:
        import json

        console.print_json(
            json.dumps(
                {
                    "ok": not breached,
                    "fail_at": fail_at,
                    "windows": windows,
                    "breached": breached,
                }
            )
        )
        if breached:
            raise typer.Exit(1)
        return

    table = Table(box=box.SIMPLE, show_edge=False)
    table.add_column("Provider")
    table.add_column("Window")
    table.add_column("Used", justify="right")

    for entry in windows:
        pct = float(entry["used_percent"])  # type: ignore[arg-type]
        style = "red" if pct >= fail_at else ("yellow" if pct >= fail_at * 0.75 else "")
        table.add_row(
            str(entry["display_name"]),
            str(entry["window"]),
            f"[{style}]{pct:.0f}%[/{style}]" if style else f"{pct:.0f}%",
        )

    console.print(table)
    if breached:
        console.print(f"\n[red]⚠ {len(breached)} window(s) at/above {fail_at:.0f}%:[/red]")
        for entry in breached:
            console.print(
                f"  · {entry['display_name']} — {entry['window']}: "
                f"{float(entry['used_percent']):.0f}%"  # type: ignore[arg-type]
            )
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
    try:
        settings = load_settings()
    except Exception as e:
        console.print(f"[red]Error loading configuration:[/red] {str(e)}")
        console.print("[dim]Run 'llm-usage setup' to configure the application.[/dim]")
        raise typer.Exit(1)
    
    try:
        with console.status("Collecting usage from providers…"):
            report = collect_all_cached(settings, days=days, force_refresh=fresh)
    except Exception as e:
        console.print(f"[red]Error collecting usage data:[/red] {str(e)}")
        console.print("[dim]Try running 'llm-usage doctor' to diagnose provider issues.[/dim]")
        console.print("[dim]For more details, set LLM_USAGE_DEBUG=1 or LLM_USAGE_VERBOSE=1.[/dim]")
        raise typer.Exit(1)

    if provider:
        want = provider.lower().strip()
        report.providers = [
            p
            for p in report.providers
            if p.provider.value == want or want in p.display_name.lower()
        ]
        if not report.providers:
            console.print(f"[red]No provider matching[/red] {provider!r}")
            console.print("[dim]Available providers: claude, openai, codex, grok, cursor, gemini, openrouter, cohere, mistral, replicate, huggingface[/dim]")
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
    cost_footer = _combined_cost_label(report)
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
            f"{_combined_cost_label(report)}"
        )
    if report.has_estimated_cost:
        from llm_usage.pricing import PRICES_AS_OF

        console.print(
            f"[dim]~ = estimated from public list prices "
            f"(pricing table as of {PRICES_AS_OF}) — not an invoice.[/dim]"
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


def _combined_cost_label(report: AggregateReport) -> str:
    """Footer / summary line that keeps billed vs estimated distinct."""
    billed = report.billed_cost_usd
    estimated = report.estimated_cost_usd
    if billed is None and estimated is None:
        return "—"
    if billed is not None and estimated is not None:
        return f"${billed:,.2f} + ~${estimated:,.2f}"
    if estimated is not None:
        return f"~${estimated:,.2f}"
    return f"${billed:,.2f}"


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
