"""Shared helpers for provider collectors."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_error_str(exc: BaseException, limit: int = 200) -> str:
    """Render an exception for display in errors/notes: strip query strings
    (which can carry API keys, e.g. `?key=...`) from httpx URLs and cap
    length so a raw response/traceback dump doesn't flood the UI."""
    if isinstance(exc, httpx.HTTPStatusError):
        url = exc.request.url
        location = f"{url.scheme}://{url.host}{url.path}"
        body = exc.response.text
        msg = f"HTTP {exc.response.status_code} for {location}"
        if body:
            msg += f": {body}"
    elif isinstance(exc, httpx.RequestError):
        url = exc.request.url if exc.request is not None else None
        location = f"{url.scheme}://{url.host}{url.path}" if url is not None else "request"
        msg = f"{type(exc).__name__} for {location}"
    else:
        msg = str(exc)
    if len(msg) > limit:
        msg = msg[: limit - 1] + "…"
    return msg


def day_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
