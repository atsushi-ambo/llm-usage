import json
from datetime import date
from pathlib import Path

from llm_usage.providers.gemini import _scan_local_logs


def test_undated_rows_are_not_counted_into_every_window(tmp_path: Path):
    chats_dir = tmp_path / "tmp" / "abc" / "chats"
    chats_dir.mkdir(parents=True)
    chat_file = chats_dir / "session.json"
    # No timestamp field anywhere on this message — only the file's mtime
    # can place it in a day bucket.
    chat_file.write_text(
        json.dumps(
            [
                {
                    "model": "gemini-2.5-flash",
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
                }
            ]
        )
    )

    # A window that can't possibly contain "now" (the file's mtime) should
    # see none of this usage.
    far_past = _scan_local_logs(tmp_path, date(2000, 1, 1), date(2000, 1, 2))
    assert far_past["requests"] == 0
    assert far_past["input_tokens"] == 0

    # A window spanning today (the file's mtime) should count it exactly once.
    today = date.today()
    current = _scan_local_logs(tmp_path, today, today)
    assert current["requests"] == 1
    assert current["input_tokens"] == 100
    assert current["output_tokens"] == 20


def test_dated_rows_use_their_own_timestamp_not_mtime(tmp_path: Path):
    chats_dir = tmp_path / "tmp" / "abc" / "chats"
    chats_dir.mkdir(parents=True)
    chat_file = chats_dir / "session.json"
    chat_file.write_text(
        json.dumps(
            [
                {
                    "model": "gemini-2.5-flash",
                    "timestamp": "2026-07-01T12:00:00Z",
                    "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 10},
                }
            ]
        )
    )

    result = _scan_local_logs(tmp_path, date(2026, 7, 1), date(2026, 7, 10))
    assert result["requests"] == 1
    assert result["input_tokens"] == 50

    outside = _scan_local_logs(tmp_path, date(2020, 1, 1), date(2020, 1, 2))
    assert outside["requests"] == 0
