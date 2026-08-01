"""Optional audit logging for llm-usage operations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class AuditEventType(str, Enum):
    API_CALL = "api_call"
    DATA_COLLECTION = "data_collection"
    CONFIG_CHANGE = "config_change"
    EXPORT = "export"
    SCHEDULE_RUN = "schedule_run"
    ERROR = "error"


class AuditEvent:
    def __init__(
        self,
        event_type: AuditEventType,
        provider: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
        error_message: str | None = None,
    ):
        self.timestamp = datetime.now(timezone.utc)
        self.event_type = event_type
        self.provider = provider
        self.details = details or {}
        self.success = success
        self.error_message = error_message

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "provider": self.provider,
            "details": self.details,
            "success": self.success,
            "error_message": self.error_message,
        }


def _audit_log_dir() -> Path:
    from llm_usage.quota import cache_dir

    return cache_dir() / "audit"


def _get_audit_log_file() -> Path:
    audit_dir = _audit_log_dir()
    audit_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return audit_dir / f"audit_{today}.jsonl"


def log_event(event: AuditEvent) -> None:
    try:
        log_file = _get_audit_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict()) + "\n")
    except OSError:
        pass


def log_api_call(
    provider: str, endpoint: str, success: bool, error_message: str | None = None
) -> None:
    log_event(
        AuditEvent(
            event_type=AuditEventType.API_CALL,
            provider=provider,
            details={"endpoint": endpoint},
            success=success,
            error_message=error_message,
        )
    )


def log_data_collection(
    provider: str,
    tokens_collected: int,
    success: bool,
    error_message: str | None = None,
) -> None:
    log_event(
        AuditEvent(
            event_type=AuditEventType.DATA_COLLECTION,
            provider=provider,
            details={"tokens_collected": tokens_collected},
            success=success,
            error_message=error_message,
        )
    )


def log_config_change(setting_name: str, old_value: Any, new_value: Any) -> None:
    log_event(
        AuditEvent(
            event_type=AuditEventType.CONFIG_CHANGE,
            details={
                "setting": setting_name,
                "old_value": str(old_value),
                "new_value": str(new_value),
            },
        )
    )


def log_export(
    format: str, records: int, success: bool, error_message: str | None = None
) -> None:
    log_event(
        AuditEvent(
            event_type=AuditEventType.EXPORT,
            details={"format": format, "records": records},
            success=success,
            error_message=error_message,
        )
    )


def log_schedule_run(
    schedule_name: str, success: bool, error_message: str | None = None
) -> None:
    log_event(
        AuditEvent(
            event_type=AuditEventType.SCHEDULE_RUN,
            details={"schedule_name": schedule_name},
            success=success,
            error_message=error_message,
        )
    )


def log_error(
    provider: str | None,
    error_message: str,
    context: dict[str, Any] | None = None,
) -> None:
    log_event(
        AuditEvent(
            event_type=AuditEventType.ERROR,
            provider=provider,
            details=context or {},
            success=False,
            error_message=error_message,
        )
    )


def read_audit_logs(days: int = 7) -> list[dict[str, Any]]:
    audit_dir = _audit_log_dir()
    if not audit_dir.exists():
        return []

    logs: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    for i in range(max(1, days)):
        day = today - timedelta(days=i)
        log_file = audit_dir / f"audit_{day.isoformat()}.jsonl"
        if not log_file.exists():
            continue
        try:
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        logs.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
    return logs


def cleanup_old_logs(retention_days: int = 30) -> int:
    audit_dir = _audit_log_dir()
    if not audit_dir.exists():
        return 0

    removed = 0
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    for log_file in audit_dir.glob("audit_*.jsonl"):
        try:
            date_str = log_file.stem.replace("audit_", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if file_date < cutoff:
                log_file.unlink()
                removed += 1
        except (ValueError, OSError):
            pass
    return removed
