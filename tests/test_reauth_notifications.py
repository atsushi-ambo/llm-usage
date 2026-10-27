"""Auth reminders distinguish login rejection from outages and survive restarts."""

import json
from datetime import date
from unittest.mock import Mock

import httpx
import pytest

from llm_usage.config import Settings
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind
from llm_usage.menubar_core import (
    LoginAction,
    reauth_login_action,
    reauth_notifications,
    provider_issue_label,
)
from llm_usage.providers import claude, codex, xai
from llm_usage.providers.base import ReauthenticationRequired
from llm_usage.serialize import slim_report_for_menubar

DAY = date(2026, 10, 26)


@pytest.mark.parametrize(
    ("pid", "expected"),
    [
        ("muse", LoginAction("browser", ("https://muse.ai/",))),
        ("claude", LoginAction("terminal", ("claude", "auth", "login"))),
        ("codex", LoginAction("terminal", ("codex", "login"))),
        ("grok", LoginAction("terminal", ("grok", "login", "--oauth"))),
    ],
)
def test_login_allowlist(pid, expected):
    assert reauth_login_action({"provider": pid, "command": "untrusted"}) == expected


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        "claude",
        {"provider": None},
        {"provider": []},
        {"provider": "unknown"},
        {"provider": "claude; touch /tmp/untrusted"},
    ],
)
def test_login_allowlist_rejects_missing_or_unknown_data(data):
    assert reauth_login_action(data) is None
    assert reauth_login_action() is None


def report(required=True, *, source=SourceKind.SUBSCRIPTION, **meta):
    return AggregateReport(
        period_start=DAY,
        period_end=DAY,
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude Code",
                source=source,
                meta={"reauth_required": required, **meta},
            )
        ],
    )


def test_reminders_persist_across_restarts_and_repeat_at_most_daily():
    state = {}
    slim = slim_report_for_menubar(report())
    assert reauth_notifications(slim, state, now=10000) == [("claude", "Claude")]
    restored = json.loads(json.dumps(state))
    assert reauth_notifications(slim, restored, now=10030) == []
    assert reauth_notifications(slim, restored, now=96399) == []
    assert reauth_notifications(slim, restored, now=96400) == [("claude", "Claude")]
    assert provider_issue_label(slim.providers[0]) == "Sign-in required"


def test_only_confirmed_recovery_rearms_reminder():
    state = {}
    reauth_notifications(report(), state, now=10000)
    unavailable = report(False, source=SourceKind.UNAVAILABLE)
    assert reauth_notifications(unavailable, state, now=10030) == []
    assert state == {"claude": 10000}
    stale = report(False, quota_stale=True)
    reauth_notifications(stale, state, now=10060)
    assert state == {"claude": 10000}
    reauth_notifications(report(False), state, now=10090)
    assert state == {}
    assert reauth_notifications(report(), state, now=10100) == [("claude", "Claude")]


def test_errors_without_auth_signal_do_not_notify():
    r = report(False)
    for error in ["HTTP 429", "timeout", "HTTP 403", "HTTP 500"]:
        r.providers[0].errors = [error]
        assert reauth_notifications(r, {}, now=10000) == []


def test_expired_claude_token_sets_signal_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(
        claude,
        "_read_claude_cred_meta",
        lambda p: {
            "access_token": "synthetic",
            "expires_at": 1,
            "source": "file",
            "plan": "pro",
        },
    )
    monkeypatch.setattr(
        claude, "_fetch_oauth_usage_cached", Mock(side_effect=AssertionError("expired token"))
    )
    s = Settings(_env_file=None, claude_projects_dir=tmp_path)
    r = claude.collect_claude(s, DAY, DAY, quota_only=True)
    assert r.meta["reauth_required"] is True


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_codex_only_unauthorized_sets_signal(monkeypatch, status):
    monkeypatch.setattr(codex, "_read_codex_auth", lambda p: {"access_token": "synthetic"})
    request = httpx.Request("GET", "https://chatgpt.com/backend-api/wham/usage")
    response = httpx.Response(status, request=request)
    monkeypatch.setattr(
        codex,
        "_fetch_wham_usage",
        Mock(
            side_effect=httpx.HTTPStatusError(
                "request rejected",
                request=request,
                response=response,
            )
        ),
    )
    r = codex.collect_codex(Settings(_env_file=None), DAY, DAY, quota_only=True)
    assert bool(r.meta.get("reauth_required")) == (status == 401)


def test_grok_auth_rejection_sets_signal_but_outage_does_not(monkeypatch):
    s = Settings(_env_file=None)
    monkeypatch.setattr(
        xai, "_fetch_live_credits", Mock(side_effect=ReauthenticationRequired("auth rejected"))
    )
    r = xai.collect_xai(s, DAY, DAY, quota_only=True)
    assert r.meta["reauth_required"] is True
    monkeypatch.setattr(
        xai, "_fetch_live_credits", Mock(side_effect=RuntimeError("temporary failure"))
    )
    r = xai.collect_xai(s, DAY, DAY, quota_only=True)
    assert not r.meta.get("reauth_required")


def test_unbundled_app_gets_notification_identity_without_files(monkeypatch):
    import sys
    from types import SimpleNamespace
    from llm_usage.menubar import _configure_notification_identity

    info = {}

    class Info:
        def setObject_forKey_(self, value, key):
            info[key] = value

    bundle = SimpleNamespace(
        bundleIdentifier=lambda: info.get("CFBundleIdentifier"),
        infoDictionary=lambda: Info(),
    )
    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        SimpleNamespace(
            NSBundle=SimpleNamespace(mainBundle=lambda: bundle),
        ),
    )
    assert _configure_notification_identity()
    assert info["CFBundleIdentifier"] == "com.llm-usage.menubar"
    assert info["CFBundleDisplayName"] == "llm-usage"
    info["CFBundleIdentifier"] = "existing.bundle"
    assert _configure_notification_identity()
    assert info["CFBundleIdentifier"] == "existing.bundle"


def test_auth_notification_carries_provider_and_click_starts_worker(monkeypatch):
    import sys
    from llm_usage import menubar

    rumps = Mock()
    rumps.notifications.side_effect = lambda callback: callback
    monkeypatch.setitem(sys.modules, "rumps", rumps)
    monkeypatch.setattr(menubar, "_configure_notification_identity", lambda: True)
    monkeypatch.setattr(menubar, "_load_prefs", lambda: {})
    monkeypatch.setattr(menubar, "_save_prefs", Mock())
    monkeypatch.setattr(menubar, "_collect_menubar_report", lambda **kw: report())
    monkeypatch.setattr(menubar.time, "sleep", Mock(side_effect=StopIteration))

    def start_one_collection(*, target, daemon):
        def start():
            try:
                target()
            except StopIteration:
                pass

        return Mock(start=start)

    monkeypatch.setattr(menubar.threading, "Thread", start_one_collection)
    rumps.Timer.side_effect = lambda callback, interval: Mock(start=lambda: callback())
    menubar.run_menubar()
    rumps.notification.assert_called_once_with(
        title="Claude — 再ログインが必要です",
        subtitle="llm-usage",
        message="使用量を取得できません。該当サービスで再ログインしてください。",
        data={"provider": "claude"},
    )

    from types import SimpleNamespace

    worker = Mock()
    monkeypatch.setattr(menubar.threading, "Thread", worker)
    callback = rumps.notifications.call_args.args[0]
    callback(SimpleNamespace(data=None))  # quota notifications carry no data
    callback(SimpleNamespace(data={"provider": "unknown"}))
    worker.assert_not_called()
    callback(SimpleNamespace(data={"provider": "claude"}))
    worker.assert_called_once_with(
        target=menubar._run_login_action,
        args=(LoginAction("terminal", ("claude", "auth", "login")),),
        daemon=True,
    )
    worker.return_value.start.assert_called_once()


@pytest.mark.parametrize("pid", ["claude", "codex", "grok"])
def test_terminal_login_uses_private_self_removing_command(monkeypatch, tmp_path, pid):
    from pathlib import Path
    from llm_usage import menubar

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(menubar.tempfile, "mkdtemp", lambda **kw: str(directory))
    action = reauth_login_action({"provider": pid})

    def open_terminal(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/open", "-a", "Terminal"]
        script = Path(argv[3])
        assert script.parent.stat().st_mode & 0o777 == 0o700
        assert script.stat().st_mode & 0o777 == 0o700
        text = script.read_text()
        assert text.startswith("#!/bin/zsh -l\n")
        assert '/bin/rm -f -- "$0"' in text
        assert '/bin/rmdir -- "${0:h}"' in text
        assert text.endswith(f"exec {' '.join(action.args)}\n")
        assert "shell" not in kwargs
        assert kwargs["timeout"] == 10

    opener = Mock(side_effect=open_terminal)
    monkeypatch.setattr(menubar.subprocess, "run", opener)
    menubar._run_login_action(action)
    opener.assert_called_once()
    assert (directory / "login.command").exists()  # Terminal reads asynchronously


def test_muse_login_falls_back_when_chrome_is_unavailable(monkeypatch):
    import subprocess
    from llm_usage import menubar

    opener = Mock(side_effect=[subprocess.CalledProcessError(1, "open"), None])
    monkeypatch.setattr(menubar.subprocess, "run", opener)
    menubar._run_login_action(reauth_login_action({"provider": "muse"}))
    assert [call.args[0] for call in opener.call_args_list] == [
        ["/usr/bin/open", "-a", "Google Chrome", "https://muse.ai/"],
        ["/usr/bin/open", "https://muse.ai/"],
    ]


def test_failed_terminal_open_is_nonfatal_and_cleans_up(monkeypatch, tmp_path):
    from llm_usage import menubar

    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(menubar.tempfile, "mkdtemp", lambda **kw: str(directory))
    monkeypatch.setattr(menubar.subprocess, "run", Mock(side_effect=OSError("unavailable")))
    menubar._run_login_action(reauth_login_action({"provider": "codex"}))
    assert not directory.exists()
