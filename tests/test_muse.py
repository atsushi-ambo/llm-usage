"""Synthetic sessions only: quota parsing, read-only auth, and bounded discovery."""

import ctypes
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from unittest.mock import Mock

import httpx
import pytest

from llm_usage.config import Settings
from llm_usage.menubar_core import active_report, display_quota, title_quota_chip
from llm_usage.models import AggregateReport, SourceKind
from llm_usage.providers import muse
from llm_usage.quota import read_json_cache, write_json_cache
from llm_usage.serialize import report_to_dict, slim_report_for_menubar

ACTION = "a" * 42
NEW_ACTION = "b" * 42
SECRET = "hatch_sess=synthetic-secret"
DAY = date(2026, 10, 26)
RESET = 1793321203


def settings(**kwargs):
    return Settings(_env_file=None, muse_cookie=SECRET, muse_chrome_import=False, **kwargs)


def subscription(percent=15, **extra):
    return {
        "usage": {"percentUsed": percent, "resetsAt": RESET},
        "tier": {"name": "Muse Free"},
        **extra,
    }


def flight(sub):
    return '0:{"a":"$@1"}\n1:' + json.dumps({"success": True, "subscription": sub}) + "\n"


def mock_client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(muse.httpx, "Client", lambda **kwargs: client)
    return client


def test_live_shape_is_visible_and_serializes_without_secrets(monkeypatch):
    write_json_cache(muse._ACTION_CACHE, {"action": ACTION})

    def handler(request):
        assert str(request.url) == "https://muse.ai/"
        assert request.headers["cookie"] == SECRET
        assert request.headers["sec-fetch-site"] == "same-origin"
        assert json.loads(request.content) == [{"includeAgreement": False}]
        return httpx.Response(200, text=flight(subscription(15, agreement={"secret": SECRET})))

    mock_client(monkeypatch, handler)
    report = muse.collect_muse(settings(), DAY, DAY, quota_only=True)
    assert report.source == SourceKind.SUBSCRIPTION
    assert report.errors == []
    assert report.total_tokens == 0 and report.cost_usd is None
    assert display_quota(report)["used_percent"] == 15
    aggregate = AggregateReport(period_start=DAY, period_end=DAY, providers=[report])
    now = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
    assert active_report(aggregate, {}, now=now).providers
    slim = slim_report_for_menubar(aggregate).providers[0]
    assert title_quota_chip(slim) == "Muse 15%"
    serialized = json.dumps(report_to_dict(aggregate))
    assert SECRET not in serialized
    assert "agreement" not in serialized
    assert read_json_cache(muse._ACTION_CACHE) == {"action": ACTION}


@pytest.mark.parametrize("percent", [0, 100, 12.5])
def test_valid_percent_boundaries(monkeypatch, percent):
    write_json_cache(muse._ACTION_CACHE, {"action": ACTION})
    mock_client(monkeypatch, lambda req: httpx.Response(200, text=flight(subscription(percent))))
    r = muse.collect_muse(settings(), DAY, DAY)
    assert r.meta["quota"]["used_percent"] == percent


@pytest.mark.parametrize("percent", [None, True, -1, 101, "15", float("nan"), float("inf")])
def test_invalid_percent_is_not_zero_or_cached(monkeypatch, percent):
    mock_client(monkeypatch, lambda req: httpx.Response(200, text=flight(subscription(percent))))
    monkeypatch.setattr(muse._Website, "discover", lambda *args: ACTION)
    r = muse.collect_muse(settings(), DAY, DAY)
    assert r.source == SourceKind.UNAVAILABLE
    assert "quota" not in r.meta and r.errors
    assert read_json_cache(muse._ACTION_CACHE) is None


@pytest.mark.parametrize("reset", [None, True, "tomorrow", float("inf"), 1e30])
def test_invalid_reset_rejected(monkeypatch, reset):
    sub = subscription()
    sub["usage"]["resetsAt"] = reset
    write_json_cache(muse._ACTION_CACHE, {"action": ACTION})
    mock_client(monkeypatch, lambda req: httpx.Response(200, text=flight(sub)))
    r = muse.collect_muse(settings(), DAY, DAY)
    assert r.errors and "quota" not in r.meta


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_rejection_does_not_follow_redirect_or_echo_body(monkeypatch, status):
    calls = []
    write_json_cache(muse._ACTION_CACHE, {"action": ACTION})

    def handler(req):
        calls.append(req)
        return httpx.Response(status, text=SECRET, headers={"location": "https://evil.test/"})

    mock_client(monkeypatch, handler)
    r = muse.collect_muse(settings(), DAY, DAY)
    assert len(calls) == 1
    assert r.errors and SECRET not in str(r.model_dump())
    assert r.source == SourceKind.UNAVAILABLE and "quota" not in r.meta


def test_deploy_refresh_discovers_once_and_static_requests_have_no_cookies(monkeypatch):
    write_json_cache(muse._ACTION_CACHE, {"action": ACTION})
    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "POST":
            if req.headers["next-action"] == ACTION:
                return httpx.Response(404, text="Server action not found.")
            assert req.headers["next-action"] == NEW_ACTION
            return httpx.Response(200, text=flight(subscription()))
        if req.url.path == "/":
            return httpx.Response(
                200,
                text='<script src="/_next/static/chunks/app.js"></script>',
                headers={"set-cookie": "other-secret=must-not-leak; Path=/"},
            )
        assert not req.headers.get("cookie")
        if req.url.path.endswith("app.js"):
            return httpx.Response(
                200,
                text="e.A(77).then(({preloadHatchSettingsData:x})=>x);"
                '([77,e=>{e.v(e=>Promise.all(["static/chunks/settings.js"]))}])',
            )
        return httpx.Response(
            200, text=f'createServerReference("{NEW_ACTION}",x,null,"fetchSubscriptionAction")'
        )

    mock_client(monkeypatch, handler)
    r = muse.collect_muse(settings(), DAY, DAY)
    assert r.errors == []
    assert len(calls) == 5
    assert read_json_cache(muse._ACTION_CACHE) == {"action": NEW_ACTION}


def test_changed_website_fails_bounded_without_caching(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, text="<html>changed</html>")

    mock_client(monkeypatch, handler)
    r = muse.collect_muse(settings(), DAY, DAY)
    assert r.errors and len(calls) == 1
    assert read_json_cache(muse._ACTION_CACHE) is None


def test_oversize_response_and_unsafe_path_rejected():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
        )
    ) as client:
        site = muse._Website(client)
        with pytest.raises(muse.MuseError, match="size"):
            site.request("GET", "/")
        with pytest.raises(muse.MuseError, match="asset path"):
            site.request("GET", "//evil.test/private")


def test_unconfigured_does_not_network_or_access_keychain(monkeypatch, tmp_path):
    monkeypatch.setattr(muse.sys, "platform", "darwin")
    password = Mock(side_effect=AssertionError("no keychain access"))
    monkeypatch.setattr(muse, "_chrome_password", password)
    mock_client(monkeypatch, lambda req: pytest.fail("no network"))
    r = muse.collect_muse(Settings(_env_file=None, muse_chrome_dir=tmp_path), DAY, DAY)
    assert r.errors == [] and r.notes
    password.assert_not_called()


def test_chrome_import_queries_only_muse_unexpired_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(muse.sys, "platform", "darwin")
    profile = tmp_path / "Default"
    profile.mkdir()
    with sqlite3.connect(profile / "Cookies") as db:
        db.execute("CREATE TABLE meta(key TEXT,value TEXT)")
        db.execute("INSERT INTO meta VALUES('version','24')")
        db.execute(
            "CREATE TABLE cookies(host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,expires_utc INTEGER)"
        )
        db.executemany(
            "INSERT INTO cookies VALUES(?,?,?,?,?)",
            [
                (".muse.ai", "hatch_sess", "valid", b"", 0),
                (".muse.ai", "unrelated", "private", b"", 0),
                (".evil.test", "hatch_sess", "wrong-domain", b"", 0),
                ("muse.ai", "hatch_sess", "expired", b"", 1),
            ],
        )
    password = Mock(side_effect=AssertionError("plaintext needs no keychain"))
    monkeypatch.setattr(muse, "_chrome_password", password)
    assert muse.chrome_cookie_headers(Settings(_env_file=None, muse_chrome_dir=tmp_path)) == [
        "hatch_sess=valid"
    ]
    password.assert_not_called()


@pytest.mark.skipif(muse.sys.platform != "darwin", reason="macOS CommonCrypto only")
def test_cookie_decrypt_validates_host_hash():
    password, host, value = b"fake-safe-storage", ".muse.ai", b"fake-session"
    plain = hashlib.sha256(host.encode()).digest() + value
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
    crypt = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib").CCCrypt
    crypt.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    out = ctypes.create_string_buffer(len(plain) + 16)
    written = ctypes.c_size_t()
    assert (
        crypt(0, 0, 1, key, 16, b" " * 16, plain, len(plain), out, len(out), ctypes.byref(written))
        == 0
    )
    encrypted = b"v10" + out.raw[: written.value]
    assert muse._decrypt_cookie(encrypted, password, host, 24) == value.decode()
    with pytest.raises(muse.MuseError, match="domain check"):
        muse._decrypt_cookie(encrypted, password, "evil.test", 24)


def test_invalid_cookie_never_networks(monkeypatch):
    mock_client(monkeypatch, lambda req: pytest.fail("must reject before network"))
    for cookie in ["hatch_sess=x\r\nAuthorization: secret", "other=x", "hatch_sess="]:
        r = muse.collect_muse(Settings(_env_file=None, muse_cookie=cookie), DAY, DAY)
        assert r.errors and cookie not in str(r.errors)
