"""CLI entrypoint: `llm-usage`."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from llm_usage import __version__
from llm_usage.config import load_settings
from llm_usage.models import AggregateReport, ProviderReport, SourceKind
from llm_usage.providers import collect_all

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
        help="Filter: claude, openai, grok, cursor, gemini",
    ),
    version: bool = typer.Option(False, "--version", help="Show version and exit"),
) -> None:
    """Show a unified usage summary (default command)."""
    if version:
        console.print(f"llm-usage {__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is not None:
        return
    _show(days=days, fmt=fmt, provider=provider)


@app.command("show")
def show_cmd(
    days: Optional[int] = typer.Option(None, "--days", "-d"),
    fmt: OutputFormat = typer.Option(OutputFormat.table, "--format", "-f"),
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
) -> None:
    """Collect and display usage for all configured providers."""
    _show(days=days, fmt=fmt, provider=provider)


@app.command("dashboard")
def dashboard_cmd(
    port: Optional[int] = typer.Option(None, "--port", help="HTTP port"),
    host: Optional[str] = typer.Option(None, "--host", help="Bind host"),
    days: Optional[int] = typer.Option(None, "--days", "-d"),
) -> None:
    """Start a local web dashboard (http://127.0.0.1:8765)."""
    settings = load_settings()
    bind_host = host or settings.host
    bind_port = port or settings.port
    if days is not None:
        # temporarily override via env for the app process
        import os

        os.environ["LLM_USAGE_DAYS"] = str(days)

    console.print(
        Panel.fit(
            f"[bold]llm-usage dashboard[/bold]\n"
            f"Open [link=http://{bind_host}:{bind_port}]http://{bind_host}:{bind_port}[/link]\n"
            f"Press Ctrl+C to stop.",
            border_style="cyan",
        )
    )
    import uvicorn

    uvicorn.run(
        "llm_usage.dashboard.app:app",
        host=bind_host,
        port=bind_port,
        log_level="info",
        reload=False,
    )


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


@app.command("export")
def export_cmd(
    output: Path = typer.Option(Path("usage-report.json"), "--output", "-o"),
    days: Optional[int] = typer.Option(None, "--days", "-d"),
) -> None:
    """Write a JSON usage report to disk."""
    settings = load_settings()
    with console.status("Collecting usage…"):
        report = collect_all(settings, days=days)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[green]Wrote[/green] {output.resolve()}")


def _show(
    days: Optional[int],
    fmt: OutputFormat,
    provider: Optional[str],
) -> None:
    settings = load_settings()
    with console.status("Collecting usage from providers…"):
        report = collect_all(settings, days=days)

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
        console.print_json(report.model_dump_json())
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
            if p.total_tokens == 0 and p.cost_usd is None:
                console.print(f"[dim]· {p.display_name}: {note}[/dim]")

    if report.total_cost_usd is not None:
        console.print(
            f"\n[bold green]Combined cost (where known):[/bold green] "
            f"${report.total_cost_usd:,.2f}"
        )
    console.print(
        f"[dim]Tip: llm-usage dashboard  ·  llm-usage status  ·  llm-usage --format json[/dim]"
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
