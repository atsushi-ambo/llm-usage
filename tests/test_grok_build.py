import json
from datetime import date
from pathlib import Path

from llm_usage.providers.xai import _scan_grok_build


def test_scan_grok_build_logs(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "unified.jsonl"
    rows = [
        {
            "ts": "2026-07-12T08:00:00Z",
            "msg": "billing: fetched credits config",
            "ctx": {
                "subscriptionTier": "X Premium",
                "config": {
                    "creditUsagePercent": 12.0,
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "start": "2026-07-08T00:00:00Z",
                        "end": "2026-07-15T00:00:00Z",
                    },
                },
            },
        },
        {
            "ts": "2026-07-12T09:00:00Z",
            "sid": "abc",
            "msg": "shell.turn.inference_done",
            "ctx": {
                "prompt_tokens": 1000,
                "cached_prompt_tokens": 800,
                "completion_tokens": 100,
                "reasoning_tokens": 20,
            },
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    result = _scan_grok_build(tmp_path, date(2026, 7, 1), date(2026, 7, 20))
    assert result["requests"] == 1
    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 100
    assert result["cache_read_tokens"] == 800
    assert result["billing"]["subscription_tier"] == "X Premium"
    assert result["billing"]["credit_usage_percent"] == 12.0
