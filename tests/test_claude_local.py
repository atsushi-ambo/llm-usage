import json
from datetime import date
from pathlib import Path

import llm_usage.providers.claude as claude_module
from llm_usage.providers.claude import _scan_local_logs


def test_scan_local_logs(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    session = proj / "s1.jsonl"
    rows = [
        {
            "type": "assistant",
            "timestamp": "2026-07-01T12:00:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                },
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-07-01T13:00:00Z",
            "message": {
                "model": "<synthetic>",
                "usage": {"input_tokens": 999, "output_tokens": 999},
            },
        },
    ]
    session.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    result = _scan_local_logs(tmp_path, date(2026, 7, 1), date(2026, 7, 10))
    assert result["requests"] == 1
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["cache_read_tokens"] == 10
    assert result["models"][0].model == "claude-sonnet-4-6"


def test_scan_local_logs_reuses_cache_across_calls_and_windows(tmp_path: Path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    session = proj / "s1.jsonl"
    session.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-01T12:00:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            }
        )
        + "\n"
    )

    calls = []
    original = claude_module._parse_claude_file

    def spy(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(claude_module, "_parse_claude_file", spy)

    r1 = _scan_local_logs(tmp_path, date(2026, 7, 1), date(2026, 7, 10))
    # A different --days window over the same unchanged file should not
    # trigger a re-parse — only the (mtime, size) fingerprint matters.
    r2 = _scan_local_logs(tmp_path, date(2026, 6, 1), date(2026, 7, 31))

    assert r1["requests"] == 1
    assert r2["requests"] == 1
    assert len(calls) == 1
