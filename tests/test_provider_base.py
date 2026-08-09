"""Tests for shared provider helpers.

`safe_error_str` is a security control, not just formatting: provider errors
flow into `report.errors`, which surfaces in the CLI table, the dashboard
JSON, and `llm-usage export`. Any API key that reaches it is leaked to all
three. Gemini's model-list call used to pass the key as `?key=...`, and
httpx echoes the full URL into exception messages — these tests pin the
redaction so that regression can't come back silently.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from llm_usage.providers.base import (
    day_range,
    parse_iso_date,
    safe_error_str,
    safe_float,
    safe_int,
    utc_now,
)

_SECRET = "AIzaSyTOTALLY-NOT-A-REAL-KEY-000000000000"


def _status_error(url: str, status: int = 400, body: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_status_error_strips_query_string_with_api_key():
    exc = _status_error(f"https://generativelanguage.googleapis.com/v1beta/models?key={_SECRET}")
    msg = safe_error_str(exc)
    assert _SECRET not in msg
    assert "key=" not in msg
    # Still useful for debugging: method-agnostic host + path + status.
    assert "generativelanguage.googleapis.com/v1beta/models" in msg
    assert "400" in msg


def test_request_error_strips_query_string_too():
    request = httpx.Request("GET", f"https://api.example.com/v1/usage?token={_SECRET}")
    exc = httpx.ConnectTimeout("timed out", request=request)
    msg = safe_error_str(exc)
    assert _SECRET not in msg
    assert "ConnectTimeout" in msg
    assert "api.example.com/v1/usage" in msg


def test_long_response_body_is_truncated_to_limit():
    exc = _status_error("https://api.example.com/v1/x", body="A" * 5000)
    msg = safe_error_str(exc)
    assert len(msg) <= 200
    assert msg.endswith("…")


def test_custom_limit_is_respected():
    exc = _status_error("https://api.example.com/v1/x", body="B" * 500)
    assert len(safe_error_str(exc, limit=50)) <= 50


def test_short_body_is_preserved_for_debuggability():
    exc = _status_error("https://api.example.com/v1/x", status=403, body="quota exceeded")
    msg = safe_error_str(exc)
    assert "403" in msg
    assert "quota exceeded" in msg


def test_plain_exception_falls_back_to_str():
    assert safe_error_str(RuntimeError("no credentials found")) == "no credentials found"


def test_plain_exception_is_also_truncated():
    msg = safe_error_str(RuntimeError("x" * 999))
    assert len(msg) <= 200


# ── the small parsing helpers every collector depends on ──────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-18T12:00:00Z", date(2026, 7, 18)),
        ("2026-07-18T12:00:00+09:00", date(2026, 7, 18)),
        ("2026-07-18", date(2026, 7, 18)),
        # Falls back to the leading 10 chars when the full parse fails.
        ("2026-07-18 garbage", date(2026, 7, 18)),
        ("not-a-date", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_iso_date(value, expected):
    assert parse_iso_date(value) == expected


def test_day_range_is_inclusive_on_both_ends():
    days = day_range(date(2026, 7, 1), date(2026, 7, 4))
    assert days == [date(2026, 7, i) for i in (1, 2, 3, 4)]


def test_day_range_single_day_and_reversed():
    assert day_range(date(2026, 7, 1), date(2026, 7, 1)) == [date(2026, 7, 1)]
    assert day_range(date(2026, 7, 4), date(2026, 7, 1)) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), ("12", 12), (12.9, 12), ("abc", 0), ({}, 0), (True, 1)],
)
def test_safe_int(value, expected):
    assert safe_int(value) == expected


def test_safe_int_custom_default():
    assert safe_int("nope", default=7) == 7


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("1.5", 1.5), (2, 2.0), ("abc", None)],
)
def test_safe_float(value, expected):
    assert safe_float(value) == expected


def test_safe_float_custom_default():
    assert safe_float(None, default=0.0) == 0.0
    assert safe_float("abc", default=0.0) == 0.0


def test_utc_now_is_timezone_aware():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None and now.utcoffset().total_seconds() == 0
