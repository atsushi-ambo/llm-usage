"""Claude must refresh and stay visible like Codex and Grok.

Three bugs together made Claude disappear from the menu bar:
  * its OAuth usage sat behind a 15-minute cache while Codex/Grok refresh
    every 30 s menu bar poll;
  * a valid token cleared the 429 cooldown on every poll, so once
    rate limited the menu bar re-hit the endpoint every 30 s and stayed
    rate limited, while the frozen body was labeled live;
  * any failure marked the quota stale, and stale providers were hidden
    even though their 5-hour/weekly windows were still open.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

import llm_usage.providers.claude as claude_module
from llm_usage import quota
from llm_usage.config import Settings
from llm_usage.menubar import REFRESH_SECONDS
from llm_usage.menubar_core import active_report, provider_issue_label, quota_window_open
from llm_usage.models import AggregateReport



class _FakeAPI:
    """Stand-in for httpx.Client that counts calls to the OAuth usage endpoint."""

    def __init__(self) -> None:
        self.calls = 0
        self.status = 200
        self.headers: dict[str, str] = {}
        now = datetime.now(timezone.utc)
        self.body = {
            "five_hour": {"utilization": 42.0, "resets_at": (now + timedelta(hours=3)).isoformat()},
            "seven_day": {"utilization": 30.0, "resets_at": (now + timedelta(days=4)).isoformat()},
        }

    def client(self, *a, **k):
        api = self

        class _Resp:
            status_code = api.status
            headers = api.headers

            def raise_for_status(self):
                if self.status_code >= 400:
                    req = httpx.Request("GET", "https://api.anthropic.com/api/oauth/usage")
                    raise httpx.HTTPStatusError(
                        "err", request=req, response=httpx.Response(self.status_code, request=req)
                    )

            def json(self):
                return api.body

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **k):
                api.calls += 1
                return _Resp()

        return _Client()


@pytest.fixture
def api(monkeypatch) -> _FakeAPI:
    fake = _FakeAPI()
    monkeypatch.setattr(httpx, "Client", fake.client)
    monkeypatch.setattr(
        claude_module,
        "_read_claude_cred_meta",
        lambda _p: {
            "access_token": "tok",
            "plan": "max",
            "source": "keychain",
            "expires_at": time.time() + 8 * 3600,
        },
    )
    return fake


def _poll():
    return claude_module.collect_claude(
        Settings(_env_file=None), date.today(), date.today(), quota_only=True
    )


def _age_cache(seconds: float) -> None:
    path = quota.cache_dir() / "claude_oauth_usage.json"
    data = quota._read_cache_file("claude_oauth_usage.json")
    assert data is not None
    data["_cached_at"] = time.time() - seconds
    quota.atomic_write_json(path, data)


def _visible(report) -> bool:
    agg = AggregateReport(
        period_start=report.period_start, period_end=report.period_end, providers=[report]
    )
    return bool(active_report(agg, {}, now=time.time()).providers)


def test_cache_ttl_matches_menubar_poll():
    """Same cadence as Codex/Grok, which fetch live every menu bar poll."""
    assert claude_module._OAUTH_CACHE_TTL_S <= REFRESH_SECONDS


def test_next_poll_after_ttl_fetches_fresh_numbers(api):
    assert _visible(_poll())
    assert api.calls == 1

    api.body["five_hour"]["utilization"] = 55.0
    _age_cache(REFRESH_SECONDS)
    report = _poll()
    assert api.calls == 2
    assert report.meta["quota"]["used_percent"] == 55.0
    assert not report.meta.get("quota_stale")


def test_429_backoff_is_honored_across_polls(api):
    _poll()
    _age_cache(REFRESH_SECONDS)
    api.status = 429
    api.headers = {"retry-after": "600"}

    for _ in range(5):
        _poll()

    # One request hits the 429; every later poll respects the cooldown.
    assert api.calls == 2


def test_rate_limited_claude_stays_visible_marked_cached(api):
    _poll()
    _age_cache(REFRESH_SECONDS)
    api.status = 429

    report = _poll()
    assert report.meta.get("quota_stale") is True  # not passed off as live
    assert report.meta["quota"]["used_percent"] == 42.0
    assert _visible(report)
    assert provider_issue_label(report) == "Usage unavailable (cached)"


@pytest.mark.parametrize("status", [500, 503])
def test_transient_server_error_does_not_hide_claude(api, status):
    _poll()
    _age_cache(REFRESH_SECONDS)
    api.status = status
    assert _visible(_poll())


def test_stale_reading_disappears_once_its_windows_reset(api):
    """Cached numbers never outlive the period they describe."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    api.body["five_hour"]["resets_at"] = past
    api.body["seven_day"]["resets_at"] = past
    _poll()
    _age_cache(REFRESH_SECONDS)
    api.status = 429
    report = _poll()
    assert report.meta.get("quota") is None
    assert not quota_window_open(report, time.time())


def test_success_after_backoff_clears_stale_flag(api):
    _poll()
    _age_cache(REFRESH_SECONDS)
    api.status = 429
    assert _poll().meta.get("quota_stale")

    quota.clear_cooldown("claude_oauth_usage.json")  # backoff elapsed
    api.status = 200
    report = _poll()
    assert not report.meta.get("quota_stale")
