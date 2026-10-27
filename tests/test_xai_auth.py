"""Grok OAuth refresh with synthetic credentials, isolated files, and mock HTTP."""

import json
import stat
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from llm_usage.config import Settings
from llm_usage.providers import xai
from llm_usage.providers.base import ReauthenticationRequired, safe_error_str

ENTRY = "https://auth.x.ai::test-client"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
OLD_ACCESS = "synthetic-old-access"
NEW_ACCESS = "synthetic-new-access"
OLD_REFRESH = "synthetic-old-refresh"
NEW_REFRESH = "synthetic-new-refresh"
DAY = date(2026, 10, 27)


@pytest.fixture(autouse=True)
def isolated_grok(tmp_path, monkeypatch):
    home = tmp_path / "grok"
    home.mkdir()
    monkeypatch.setenv("GROK_HOME", str(home))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(xai, "datetime", Clock)
    return home


@pytest.fixture
def settings(isolated_grok):
    result = Settings(_env_file=None)
    assert result.grok_home_dir == isolated_grok
    return result


@pytest.fixture
def auth_file(isolated_grok):
    path = isolated_grok / "auth.json"
    data = {
        "metadata": {"version": 1},
        ENTRY: {
            "key": OLD_ACCESS,
            "auth_mode": "oidc",
            "refresh_token": OLD_REFRESH,
            "expires_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "test-client",
            "profile": {"name": "Synthetic user", "nested": [1, 2]},
        },
        "other-entry": {"key": "synthetic-other", "unknown": True},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture(autouse=True)
def mock_http(monkeypatch):
    client_class = httpx.Client

    def unexpected(request):
        pytest.fail("Unexpected HTTP request; real network is disabled")

    handler = unexpected

    def configure(callback):
        nonlocal handler
        handler = callback

    def make_client(**kwargs):
        return client_class(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(xai.httpx, "Client", make_client)
    return configure


def update_entry(path, **changes):
    data = json.loads(path.read_text())
    data[ENTRY].update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def refreshed(**changes):
    return httpx.Response(200, json={"access_token": NEW_ACCESS, "expires_in": 21600, **changes})


def billing():
    return httpx.Response(200, json={"config": {"creditUsagePercent": 12}})


@pytest.mark.parametrize("seconds_left", [-3600, 0, 60, 120])
@pytest.mark.parametrize("rotate", [False, True])
def test_expiring_token_refreshes_and_preserves_file(
    auth_file, settings, mock_http, monkeypatch, seconds_left, rotate,
):
    update_entry(auth_file, expires_at=(NOW + timedelta(seconds=seconds_left)).isoformat())
    original = json.loads(auth_file.read_text())
    auth_file.chmod(0o644)  # Replacement must restore private permissions.
    calls = []
    replacements = []
    import llm_usage.config as config

    real_replace = config.os.replace

    def checked_replace(source, destination):
        from pathlib import Path

        temp = Path(source)
        assert temp.parent == auth_file.parent and temp != auth_file
        assert stat.S_IMODE(temp.stat().st_mode) == 0o600
        assert json.loads(auth_file.read_text()) == original
        replacements.append(destination)
        real_replace(source, destination)

    monkeypatch.setattr(config.os, "replace", checked_replace)

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            assert str(request.url) == TOKEN_URL
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert "authorization" not in request.headers
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["refresh_token"],
                "refresh_token": [OLD_REFRESH],
                "client_id": ["test-client"],
            }
            assert all(value == 20.0 for value in request.extensions["timeout"].values())
            return refreshed(**({"refresh_token": NEW_REFRESH} if rotate else {}))
        assert str(request.url) == xai._BILLING_URL
        assert request.headers["authorization"] == f"Bearer {NEW_ACCESS}"
        return billing()

    mock_http(handler)
    report = xai.collect_xai(settings, DAY, DAY, quota_only=True)
    assert report.errors == [] and not report.meta.get("reauth_required")
    assert report.meta["quota"]["used_percent"] == 12
    expected = original
    expected[ENTRY].update(
        key=NEW_ACCESS,
        refresh_token=NEW_REFRESH if rotate else OLD_REFRESH,
        expires_at="2026-10-27T18:00:00Z",
    )
    assert json.loads(auth_file.read_text()) == expected
    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600
    assert calls == ["POST", "GET"] and replacements == [auth_file]
    assert list(auth_file.parent.iterdir()) == [auth_file]


def test_concurrent_rotation_uses_current_credentials_without_overwrite(auth_file, settings, mock_http):
    concurrent_contents = []

    def handler(request):
        if request.method == "POST":
            update_entry(auth_file, key="synthetic-cli-access", refresh_token="synthetic-cli-refresh",
                         expires_at="2026-10-27T20:00:00Z", cli_added=True)
            concurrent_contents.append(auth_file.read_bytes())
            return refreshed(refresh_token=NEW_REFRESH)
        assert request.headers["authorization"] == "Bearer synthetic-cli-access"
        return billing()

    mock_http(handler)
    assert xai._fetch_live_credits(settings.grok_home_dir)
    assert auth_file.read_bytes() == concurrent_contents[0]


def test_other_concurrent_edits_are_preserved(auth_file, settings, mock_http):
    def handler(request):
        if request.method == "POST":
            data = json.loads(auth_file.read_text())
            data[ENTRY]["profile"] = {"updated": True}
            data["added-by-cli"] = {"preserve": [1, 2, 3]}
            auth_file.write_text(json.dumps(data))
            return refreshed()
        return billing()

    mock_http(handler)
    xai._fetch_live_credits(settings.grok_home_dir)
    data = json.loads(auth_file.read_text())
    assert data[ENTRY]["profile"] == {"updated": True}
    assert data["added-by-cli"] == {"preserve": [1, 2, 3]}


@pytest.mark.parametrize("status", [400, 401])
def test_invalid_grant_requires_reauthentication(auth_file, settings, mock_http, status):
    original = auth_file.read_bytes()
    mock_http(lambda request: httpx.Response(status, json={
        "error": "invalid_grant", "error_description": OLD_REFRESH,
    }))
    with pytest.raises(ReauthenticationRequired) as error:
        xai._fetch_live_credits(settings.grok_home_dir)
    assert OLD_REFRESH not in str(error.value)
    report = xai.collect_xai(settings, DAY, DAY, quota_only=True)
    assert report.meta["reauth_required"] is True
    assert OLD_REFRESH not in str(report.model_dump())
    assert auth_file.read_bytes() == original


@pytest.mark.parametrize("error_class", [httpx.ConnectError, httpx.ReadTimeout])
def test_refresh_network_error_is_not_reauthentication(auth_file, settings, mock_http, error_class):
    original = auth_file.read_bytes()

    def handler(request):
        assert request.method == "POST"
        raise error_class("Synthetic network failure", request=request)

    mock_http(handler)
    with pytest.raises(error_class):
        xai._fetch_live_credits(settings.grok_home_dir)
    report = xai.collect_xai(settings, DAY, DAY, quota_only=True)
    assert report.errors and not report.meta.get("reauth_required")
    assert auth_file.read_bytes() == original


@pytest.mark.parametrize("status", [302, 307, 403, 429, 500, 503])
def test_refresh_http_errors_are_safe_and_never_follow_redirects(
    auth_file, settings, mock_http, status,
):
    original = auth_file.read_bytes()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, text=OLD_REFRESH,
                              headers={"location": "https://foreign.example/token"})

    mock_http(handler)
    with pytest.raises(httpx.HTTPStatusError) as error:
        xai._fetch_live_credits(settings.grok_home_dir)
    assert calls == [TOKEN_URL]
    assert error.value.response.status_code == status
    assert OLD_REFRESH not in safe_error_str(error.value)
    assert not error.value.request.content
    assert auth_file.read_bytes() == original


@pytest.mark.parametrize("changes", [
    {"expires_at": "2026-10-27T12:02:01Z"},
    {"oidc_issuer": "https://foreign.example"},
    {"oidc_issuer": "https://auth.x.ai/"},
    {"auth_mode": "api_key"},
    {"refresh_token": ""},
    {"oidc_client_id": ""},
])
def test_ineligible_or_unexpired_entry_does_not_refresh(auth_file, settings, mock_http, changes):
    update_entry(auth_file, **changes)
    original = auth_file.read_bytes()
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {OLD_ACCESS}"
        return billing()

    mock_http(handler)
    assert xai._fetch_live_credits(settings.grok_home_dir)
    assert calls == ["GET"] and auth_file.read_bytes() == original


@pytest.mark.parametrize("expiry", [None, "malformed", "2026-10-27T18:00:00Z"])
@pytest.mark.parametrize("retry_status", [200, 401])
def test_billing_401_refreshes_and_retries_only_once(
    auth_file, settings, mock_http, expiry, retry_status,
):
    update_entry(auth_file, expires_at=expiry)
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return refreshed()
        if len(calls) == 1:
            assert request.headers["authorization"] == f"Bearer {OLD_ACCESS}"
            return httpx.Response(401)
        assert request.headers["authorization"] == f"Bearer {NEW_ACCESS}"
        return billing() if retry_status == 200 else httpx.Response(retry_status)

    mock_http(handler)
    if retry_status == 401:
        with pytest.raises(ReauthenticationRequired):
            xai._fetch_live_credits(settings.grok_home_dir)
    else:
        assert xai._fetch_live_credits(settings.grok_home_dir)
    assert calls == ["GET", "POST", "GET"]


def test_foreign_issuer_is_not_refreshed_on_401(auth_file, settings, mock_http):
    update_entry(auth_file, oidc_issuer="https://foreign.example")
    original = auth_file.read_bytes()
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(401)

    mock_http(handler)
    with pytest.raises(ReauthenticationRequired):
        xai._fetch_live_credits(settings.grok_home_dir)
    assert calls == ["GET"] and auth_file.read_bytes() == original


@pytest.mark.parametrize("changes", [
    {"access_token": ""}, {"refresh_token": None}, {"expires_in": None},
    {"expires_in": -1}, {"expires_in": True}, {"expires_in": float("inf")},
])
def test_invalid_refresh_response_does_not_overwrite_auth(auth_file, settings, mock_http, changes):
    original = auth_file.read_bytes()
    # Text permits synthetic infinity without httpx's JSON encoder rejecting it first.
    payload = {"access_token": NEW_ACCESS, "expires_in": 21600, **changes}
    mock_http(lambda request: httpx.Response(200, text=json.dumps(payload)))
    with pytest.raises(RuntimeError) as error:
        xai._fetch_live_credits(settings.grok_home_dir)
    assert not isinstance(error.value, ReauthenticationRequired)
    assert auth_file.read_bytes() == original


def test_atomic_replace_failure_leaves_original_and_cleans_temp(
    auth_file, settings, mock_http, monkeypatch,
):
    import llm_usage.config as config

    original = auth_file.read_bytes()

    def fail_replace(source, destination):
        raise OSError("Synthetic replace failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    mock_http(lambda request: refreshed())
    with pytest.raises(OSError):
        xai._fetch_live_credits(settings.grok_home_dir)
    assert auth_file.read_bytes() == original
    assert list(auth_file.parent.iterdir()) == [auth_file]


def test_rejected_refresh_after_cli_rotation_uses_current_credentials(auth_file, settings, mock_http):
    def handler(request):
        if request.method == "POST":
            update_entry(auth_file, key="synthetic-cli-access", refresh_token="synthetic-cli-refresh")
            return httpx.Response(400, json={"error": "invalid_grant"})
        assert request.headers["authorization"] == "Bearer synthetic-cli-access"
        return billing()

    mock_http(handler)
    assert xai._fetch_live_credits(settings.grok_home_dir)
