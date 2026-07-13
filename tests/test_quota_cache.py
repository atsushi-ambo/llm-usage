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
    assert q["used_percent"] == 57
    assert q["plan"] == "pro"
    keys = {w["key"]: w["used_percent"] for w in q["windows"]}
    assert keys == {"five_hour": 42.0, "seven_day": 57}
