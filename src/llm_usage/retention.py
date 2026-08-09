"""Data retention policies for managing cached data and logs."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from llm_usage.audit import cleanup_old_logs
from llm_usage.logcache import prune_missing_sources
from llm_usage.quota import cache_dir


class RetentionPolicy:
    """Configuration for data retention policies."""
    
    def __init__(
        self,
        cache_retention_days: int = 90,
        audit_log_retention_days: int = 30,
        export_retention_days: int = 180,
        schedule_export_retention_days: int = 365,
        enable_auto_cleanup: bool = True,
    ):
        self.cache_retention_days = cache_retention_days
        self.audit_log_retention_days = audit_log_retention_days
        self.export_retention_days = export_retention_days
        self.schedule_export_retention_days = schedule_export_retention_days
        self.enable_auto_cleanup = enable_auto_cleanup


def cleanup_cache_files(retention_days: int) -> int:
    """Remove cache files older than the retention period.
    
    Returns the number of files removed.
    """
    cache_root = cache_dir()
    if not cache_root.exists():
        return 0
    
    removed = 0
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    # Clean logscan cache
    logscan_dir = cache_root / "logscan"
    if logscan_dir.exists():
        for cache_file in logscan_dir.rglob("*.json"):
            try:
                if cache_file.stat().st_mtime < cutoff_date.timestamp():
                    cache_file.unlink()
                    removed += 1
            except OSError:
                pass
    
    # Clean report snapshot cache
    for snapshot_file in cache_root.glob("report_snapshot*.json"):
        try:
            if snapshot_file.stat().st_mtime < cutoff_date.timestamp():
                snapshot_file.unlink()
                removed += 1
        except OSError:
                pass
    
    return removed


def cleanup_export_files(retention_days: int) -> int:
    """Remove exported report files older than the retention period.
    
    Returns the number of files removed.
    """
    cache_root = cache_dir()
    exports_dir = cache_root / "schedules" / "exports"
    
    if not exports_dir.exists():
        return 0
    
    removed = 0
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
    
    for export_file in exports_dir.rglob("*"):
        if export_file.is_file():
            try:
                if export_file.stat().st_mtime < cutoff_date.timestamp():
                    export_file.unlink()
                    removed += 1
            except OSError:
                pass
    
    # Clean empty directories
    for dir_path in sorted(exports_dir.rglob("*"), reverse=True):
        if dir_path.is_dir():
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
            except OSError:
                pass
    
    return removed


def apply_retention_policy(policy: RetentionPolicy) -> dict[str, int]:
    """Apply all retention policies and return cleanup statistics.
    
    Returns a dictionary with counts of removed items by category.
    """
    if not policy.enable_auto_cleanup:
        return {"cache": 0, "audit_logs": 0, "exports": 0, "total": 0}
    
    stats = {
        "cache": 0,
        "audit_logs": 0,
        "exports": 0,
        "total": 0,
    }
    
    # Clean cache files
    stats["cache"] = cleanup_cache_files(policy.cache_retention_days)
    
    # Clean audit logs
    stats["audit_logs"] = cleanup_old_logs(policy.audit_log_retention_days)
    
    # Clean export files
    stats["exports"] = cleanup_export_files(policy.export_retention_days)
    
    # Clean missing source cache entries
    stats["cache"] += prune_missing_sources()
    
    stats["total"] = stats["cache"] + stats["audit_logs"] + stats["exports"]
    
    return stats


def get_cache_size() -> dict[str, int]:
    """Get the size of cache directories in bytes.
    
    Returns a dictionary with sizes by category.
    """
    cache_root = cache_dir()
    if not cache_root.exists():
        return {"total": 0, "logscan": 0, "snapshots": 0, "exports": 0, "audit": 0}
    
    sizes = {
        "total": 0,
        "logscan": 0,
        "snapshots": 0,
        "exports": 0,
        "audit": 0,
    }
    
    # Calculate logscan cache size
    logscan_dir = cache_root / "logscan"
    if logscan_dir.exists():
        for item in logscan_dir.rglob("*"):
            if item.is_file():
                try:
                    sizes["logscan"] += item.stat().st_size
                except OSError:
                    pass
    
    # Calculate snapshot cache size
    for snapshot_file in cache_root.glob("report_snapshot*.json"):
        try:
            sizes["snapshots"] += snapshot_file.stat().st_size
        except OSError:
            pass
    
    # Calculate exports size
    exports_dir = cache_root / "schedules" / "exports"
    if exports_dir.exists():
        for item in exports_dir.rglob("*"):
            if item.is_file():
                try:
                    sizes["exports"] += item.stat().st_size
                except OSError:
                    pass
    
    # Calculate audit log size
    audit_dir = cache_root / "audit"
    if audit_dir.exists():
        for item in audit_dir.rglob("*"):
            if item.is_file():
                try:
                    sizes["audit"] += item.stat().st_size
                except OSError:
                    pass
    
    sizes["total"] = sum(sizes.values())
    
    return sizes


def format_size(bytes_size: int) -> str:
    """Format a byte size into human-readable format."""
    # Accumulate in a float local: dividing the int parameter in place makes
    # its type disagree with the signature (pyright reportAssignmentType).
    size = float(bytes_size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"
