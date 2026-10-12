"""Cache + rate-limit cooldown behavior in llm_usage.quota."""

from __future__ import annotations

import llm_usage.quota as quota


def _use_tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(quota, "cache_dir", lambda: tmp_path)


def test_cache_roundtrip(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    quota.write_json_cache("x.json", {"a": 1})
    assert quota.read_json_cache("x.json", max_age_s=60) == {"a": 1}


def test_cache_expiry_falls_back_to_stale(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    quota.write_json_cache("x.json", {"a": 1})
    assert quota.read_json_cache("x.json", max_age_s=0) is None
    assert quota.read_json_cache_stale("x.json") == {"a": 1}


def test_cooldown_roundtrip(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    assert quota.cooldown_remaining("x.json") == 0.0
    quota.write_cooldown("x.json", 120)
    remaining = quota.cooldown_remaining("x.json")
    assert 100 < remaining <= 120
    quota.clear_cooldown("x.json")
    assert quota.cooldown_remaining("x.json") == 0.0


def test_expired_cooldown_is_zero(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    quota.write_cooldown("x.json", -5)
    assert quota.cooldown_remaining("x.json") == 0.0


def test_claude_quota_normalizes_utilization_fraction():
    body = {
        "five_hour": {"utilization": 0.42, "resets_at": 1780000000},
        "seven_day": {"utilization": 57, "resets_at": 1780500000},
    }
    q = quota.claude_quota_from_oauth(body, plan="pro")
    # Primary is the 5-hour window (what blocks mid-session).
    assert q["used_percent"] == 42.0
    assert q["label"] == "5-hour limit"
    assert q["plan"] == "pro"
    keys = {w["key"]: w["used_percent"] for w in q["windows"]}
    assert keys == {"five_hour": 42.0, "seven_day": 57}


def test_claude_quota_falls_back_to_seven_day_without_five_hour():
    body = {
        "seven_day": {"utilization": 57, "resets_at": 1780500000},
    }
    q = quota.claude_quota_from_oauth(body, plan="pro")
    assert q["used_percent"] == 57
    assert q["label"] == "7-day limit"


def test_prune_expired_quota_drops_closed_windows():
    now = 1_800_000_000  # 2027-01-14
    q = {
        "used_percent": 57,
        "label": "5-hour limit",
        "resets_at": "2026-08-02T19:49:59+00:00",
        "windows": [
            {
                "key": "five_hour",
                "label": "5-hour",
                "used_percent": 57,
                "resets_at": "2026-08-02T19:49:59+00:00",
            },
            {
                "key": "seven_day",
                "label": "7-day",
                "used_percent": 34,
                "resets_at": "2026-08-07T00:59:59+00:00",
            },
        ],
    }
    assert quota.prune_expired_quota(q, now=now) is None


def test_prune_expired_quota_keeps_open_weekly_after_five_hour_reset():
    now = 1_791_700_000  # 2026-10-11
    weekly_reset = "2026-10-18T00:00:00+00:00"
    q = {
        "used_percent": 0,
        "label": "5-hour limit",
        "resets_at": "2026-10-11T00:00:00+00:00",
        "windows": [
            {
                "key": "five_hour",
                "label": "5-hour",
                "used_percent": 0,
                "resets_at": "2026-10-11T00:00:00+00:00",
            },
            {
                "key": "seven_day",
                "label": "7-day",
                "used_percent": 34,
                "resets_at": weekly_reset,
            },
        ],
    }
    kept = quota.prune_expired_quota(q, now=now)
    assert kept is not None
    assert kept["used_percent"] == 34
    assert kept["resets_at"] == weekly_reset
    assert [w["key"] for w in kept["windows"]] == ["seven_day"]


def test_prune_expired_quota_keeps_windows_without_reset():
    q = {
        "used_percent": 3,
        "label": "Weekly limit",
        "resets_at": "2026-10-14T03:27:29+00:00",
        "windows": [
            {"key": "grokbuild", "label": "GrokBuild", "used_percent": 2},
        ],
    }
    kept = quota.prune_expired_quota(q, now=1_791_700_000)
    assert kept is not None
    assert kept["windows"][0]["used_percent"] == 2
