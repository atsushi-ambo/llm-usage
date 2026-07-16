import json
from datetime import date
from pathlib import Path

from llm_usage.providers.codex import _scan_sessions


def test_scan_codex_sessions(tmp_path: Path):
    day_dir = tmp_path / "2026" / "07" / "01"
    day_dir.mkdir(parents=True)
    session = day_dir / "rollout.jsonl"
    rows = [
        {
            "timestamp": "2026-07-01T10:00:00Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.1-codex"},
        },
        {
            "timestamp": "2026-07-01T10:00:05Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 200,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 10,
                    }
                },
                "rate_limits": {"plan_type": "free"},
            },
        },
    ]
    session.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    result = _scan_sessions(tmp_path, date(2026, 7, 1), date(2026, 7, 10))
    assert result["requests"] == 1
    assert result["input_tokens"] == 1000
    # output_tokens (50) already includes reasoning_output_tokens (10) as a
    # subset — they must not be summed.
    assert result["output_tokens"] == 50
    assert result["cache_read_tokens"] == 200
    assert result["plan_type"] == "free"
    assert result["models"][0].model == "gpt-5.1-codex"
