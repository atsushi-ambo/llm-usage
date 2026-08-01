"""Scheduled reports for automated usage exports."""

from __future__ import annotations

import csv
import json
import re
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from llm_usage.config import load_settings
from llm_usage.models import AggregateReport
from llm_usage.providers import collect_all_cached
from llm_usage.serialize import report_to_dict

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class ScheduleFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ScheduledReport:
    """Configuration for a scheduled report."""

    def __init__(
        self,
        name: str,
        frequency: ScheduleFrequency,
        days: int = 30,
        enabled: bool = True,
        export_format: str = "csv",  # csv | json | txt
    ):
        if not _SAFE_NAME.match(name):
            raise ValueError(
                "Schedule name must be 1–64 chars: letters, digits, ._- "
                "(must start with alphanumeric)"
            )
        fmt = export_format.lower()
        if fmt == "pdf":
            # No PDF dependency; treat as plain text report.
            fmt = "txt"
        if fmt not in {"csv", "json", "txt"}:
            raise ValueError("export_format must be csv, json, or txt")
        self.name = name
        self.frequency = frequency
        self.days = max(1, min(int(days), 365))
        self.enabled = enabled
        self.export_format = fmt
        self.last_run: datetime | None = None
        self.next_run: datetime | None = None


def _schedules_dir() -> Path:
    from llm_usage.quota import cache_dir

    return cache_dir() / "schedules"


def save_schedule(schedule: ScheduledReport) -> None:
    schedules_dir = _schedules_dir()
    schedules_dir.mkdir(parents=True, exist_ok=True)
    schedule_file = schedules_dir / f"{schedule.name}.json"
    data = {
        "name": schedule.name,
        "frequency": schedule.frequency.value,
        "days": schedule.days,
        "enabled": schedule.enabled,
        "export_format": schedule.export_format,
        "last_run": schedule.last_run.isoformat() if schedule.last_run else None,
        "next_run": schedule.next_run.isoformat() if schedule.next_run else None,
    }
    schedule_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_schedule(name: str) -> ScheduledReport | None:
    if not _SAFE_NAME.match(name):
        return None
    schedule_file = _schedules_dir() / f"{name}.json"
    if not schedule_file.exists():
        return None
    try:
        data = json.loads(schedule_file.read_text(encoding="utf-8"))
        schedule = ScheduledReport(
            name=data["name"],
            frequency=ScheduleFrequency(data["frequency"]),
            days=int(data.get("days", 30)),
            enabled=bool(data.get("enabled", True)),
            export_format=str(data.get("export_format", "csv")),
        )
        if data.get("last_run"):
            schedule.last_run = datetime.fromisoformat(data["last_run"])
        if data.get("next_run"):
            schedule.next_run = datetime.fromisoformat(data["next_run"])
        return schedule
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def list_schedules() -> list[ScheduledReport]:
    schedules_dir = _schedules_dir()
    if not schedules_dir.exists():
        return []
    out: list[ScheduledReport] = []
    for schedule_file in sorted(schedules_dir.glob("*.json")):
        schedule = load_schedule(schedule_file.stem)
        if schedule:
            out.append(schedule)
    return out


def delete_schedule(name: str) -> bool:
    if not _SAFE_NAME.match(name):
        return False
    schedule_file = _schedules_dir() / f"{name}.json"
    if schedule_file.exists():
        schedule_file.unlink()
        return True
    return False


def calculate_next_run(
    frequency: ScheduleFrequency, last_run: datetime | None = None
) -> datetime:
    """Calculate the next run time based on frequency."""
    base = last_run or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)

    if frequency == ScheduleFrequency.DAILY:
        return base + timedelta(days=1)
    if frequency == ScheduleFrequency.WEEKLY:
        return base + timedelta(days=7)
    if frequency == ScheduleFrequency.MONTHLY:
        year, month = base.year, base.month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        # Clamp day for shorter months (Jan 31 → Feb 28/29).
        day = min(base.day, monthrange(year, month)[1])
        return base.replace(year=year, month=month, day=day)
    return base + timedelta(days=1)


def run_scheduled_report(schedule: ScheduledReport) -> AggregateReport:
    """Run a scheduled report, export it, and update timing."""
    settings = load_settings()
    report = collect_all_cached(settings, days=schedule.days, force_refresh=True)

    now = datetime.now(timezone.utc)
    schedule.last_run = now
    schedule.next_run = calculate_next_run(schedule.frequency, now)
    save_schedule(schedule)

    output_dir = _schedules_dir() / "exports"
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{schedule.name}_{timestamp}.{schedule.export_format}"
    export_report(report, schedule.export_format, output_path)
    return report


def export_report(report: AggregateReport, format: str, output_path: Path) -> Path:
    """Export a report as csv, json, or txt. Returns the path written."""
    fmt = format.lower()
    if fmt == "pdf":
        fmt = "txt"
        output_path = output_path.with_suffix(".txt")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        output_path.write_text(
            json.dumps(report_to_dict(report), indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path

    if fmt == "csv":
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Provider",
                    "Source",
                    "Requests",
                    "Input Tokens",
                    "Output Tokens",
                    "Total Tokens",
                    "Cost (USD)",
                ]
            )
            for provider in report.providers:
                cost = (
                    f"{provider.cost_usd:.4f}"
                    if provider.cost_usd is not None
                    else ""
                )
                writer.writerow(
                    [
                        provider.display_name,
                        provider.source.value,
                        provider.requests,
                        provider.input_tokens,
                        provider.output_tokens,
                        provider.total_tokens,
                        cost,
                    ]
                )
        return output_path

    if fmt == "txt":
        lines = [
            "LLM Usage Report",
            f"Period: {report.period_start} to {report.period_end}",
            f"Generated: {report.generated_at}",
            "",
        ]
        for provider in report.providers:
            cost = (
                f"${provider.cost_usd:.4f}"
                if provider.cost_usd is not None
                else "N/A"
            )
            lines.extend(
                [
                    provider.display_name,
                    f"  Source: {provider.source.value}",
                    f"  Requests: {provider.requests}",
                    f"  Total Tokens: {provider.total_tokens}",
                    f"  Cost: {cost}",
                    "",
                ]
            )
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    raise ValueError(f"Unsupported export format: {format}")


def check_and_run_due_schedules() -> list[tuple[str, datetime]]:
    """Check for due schedules and run them.

    Returns list of (schedule_name, run_time) for schedules that were run.
    """
    now = datetime.now(timezone.utc)
    run_info: list[tuple[str, datetime]] = []

    for schedule in list_schedules():
        if not schedule.enabled:
            continue
        if schedule.next_run and schedule.next_run <= now:
            try:
                run_scheduled_report(schedule)
                run_info.append((schedule.name, now))
            except Exception:  # noqa: BLE001
                pass
    return run_info
