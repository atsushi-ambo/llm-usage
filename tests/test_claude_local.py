import json
from datetime import date
from pathlib import Path

import httpx

import llm_usage.providers.claude as claude_module
import llm_usage.quota as quota
from llm_usage.config import Settings
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


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_expired_cached_quota_is_not_shown_as_live(monkeypatch):
    monkeypatch.setattr(
        claude_module,
        "_read_claude_cred_meta",
        lambda _p: {
            "plan": "pro",
            "access_token": "tok",
            "source": "keychain",
            "expires_at": 1,
        },
    )
    monkeypatch.setattr(
        claude_module,
        "_fetch_oauth_usage_cached",
        lambda _t: (_ for _ in ()).throw(
            RuntimeError("OAuth token rejected (401) — re-login to Claude Code")
        ),
    )
    quota.write_json_cache(
        "claude_oauth_usage.json",
        {
            "five_hour": {
                "utilization": 57.0,
                "resets_at": "2026-08-02T19:49:59.682003+00:00",
            },
            "seven_day": {
                "utilization": 34.0,
                "resets_at": "2026-08-07T00:59:59.682018+00:00",
            },
        },
    )
    report = claude_module.collect_claude(
        _settings(), date(2026, 10, 11), date(2026, 10, 11), quota_only=True
    )
    assert report.meta.get("quota") is None
    assert any("expired" in e.lower() or "401" in e for e in report.errors)
    assert not any("57%" in n for n in report.notes)


def test_oauth_401_writes_cooldown_and_does_not_return_stale(monkeypatch):
    class _Resp:
        status_code = 401
        headers: dict = {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "https://api.anthropic.com/api/oauth/usage"),
                response=httpx.Response(401),
            )

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    quota.atomic_write_json(
        quota.cache_dir() / "claude_oauth_usage.json",
        {
            "_cached_at": 1.0,
            "payload": {
                "five_hour": {
                    "utilization": 57.0,
                    "resets_at": "2026-08-02T19:49:59+00:00",
                }
            },
        },
    )
    raised = None
    try:
        claude_module._fetch_oauth_usage_cached("tok")
    except RuntimeError as exc:
        raised = str(exc)
    assert raised is not None
    assert "401" in raised
    fp = claude_module._token_fingerprint("tok")
    assert quota.cooldown_remaining("claude_oauth_auth.json", tag=fp) > 0

    # A later poll during cooldown must not treat the August body as live.
    try:
        claude_module._fetch_oauth_usage_cached("tok")
        raise AssertionError("cooldown should raise, not return stale usage")
    except RuntimeError as exc:
        assert "401" in str(exc)


def test_new_token_is_not_blocked_by_old_tokens_401_cooldown():
    """A fresh `claude` login must not wait out the previous token's backoff."""
    quota.write_cooldown(
        "claude_oauth_auth.json", 3600, tag=claude_module._token_fingerprint("old-tok")
    )
    fp_new = claude_module._token_fingerprint("new-tok")
    assert quota.cooldown_remaining("claude_oauth_auth.json", tag=fp_new) == 0.0


def test_token_fingerprint_does_not_contain_the_token():
    fp = claude_module._token_fingerprint("sk-ant-oat01-SECRET")
    assert "SECRET" not in fp and len(fp) == 16


def test_keychain_lookup_selects_current_user(monkeypatch):
    import getpass
    import platform
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(getpass, "getuser", lambda: "test-user")

    def lookup(args, **kwargs):
        # A service-only lookup returns the empty entry that caused the bug.
        account_scoped = args[2:4] == ["-a", "test-user"]
        token = "synthetic-token" if account_scoped else None
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"claudeAiOauth": {"accessToken": token}})
        )

    monkeypatch.setattr(subprocess, "run", lookup)
    assert claude_module._read_claude_keychain_oauth() == {"accessToken": "synthetic-token"}


def test_keychain_lookup_does_not_use_another_users_credentials(monkeypatch):
    import getpass
    import platform
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(getpass, "getuser", lambda: "test-user")
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=44, stdout="")
    )
    assert claude_module._read_claude_keychain_oauth() is None
