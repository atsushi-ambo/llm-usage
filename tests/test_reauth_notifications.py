"""Auth reminders distinguish login rejection from outages and survive restarts."""

import json
from datetime import date
from unittest.mock import Mock

import httpx
import pytest

from llm_usage.config import Settings
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind
from llm_usage.menubar_core import reauth_notifications, provider_issue_label
from llm_usage.providers import claude, codex, xai
from llm_usage.providers.base import ReauthenticationRequired
from llm_usage.serialize import slim_report_for_menubar

DAY = date(2026, 10, 26)


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
