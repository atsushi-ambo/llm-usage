"""Read muse.ai's weekly allowance; never retain cookies or billing payloads.

The read-only settings action is undocumented and changes between deployments.
Only its public action ID is cached. Chrome import queries only hatch_sess for
muse.ai, and uses macOS CommonCrypto in memory (no key in process arguments).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

import httpx

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.quota import read_json_cache, write_json_cache

_ORIGIN = "https://muse.ai"
_ACTION_CACHE = "muse_settings_action.json"
_ACTION_ID = re.compile(r"[0-9a-f]{40,64}")
_CHUNKS = re.compile(r"static/chunks/[\w~.-]+\.js")
_REFERENCE = re.compile(r'"([0-9a-f]{40,64})",[^"\n]{0,200}"fetchSubscriptionAction"')
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/143.0.0.0 Safari/537.36"


class MuseError(Exception):
    """Only fixed, non-sensitive messages may be surfaced to users."""


def _decrypt_cookie(encrypted: bytes, password: bytes, host: str, version: int) -> str:
    if not encrypted.startswith(b"v10"):
        raise MuseError("Unsupported Chrome cookie encryption. Use MUSE_COOKIE instead.")
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
    crypto = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
    crypt = crypto.CCCrypt
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
    crypt.restype = ctypes.c_int32
    payload = encrypted[3:]
    output = ctypes.create_string_buffer(len(payload) + 16)
    written = ctypes.c_size_t()
    status = crypt(
        1,
        0,
        1,
        key,
        len(key),
        b" " * 16,
        payload,
        len(payload),
        output,
        len(output),
        ctypes.byref(written),
    )
    if status != 0:
        raise MuseError("Could not decrypt Muse's Chrome session. Use MUSE_COOKIE instead.")
    plain = output.raw[: written.value]
    if version >= 24:
        if plain[:32] != hashlib.sha256(host.encode()).digest():
            raise MuseError("Muse Chrome cookie failed its domain check.")
        plain = plain[32:]
    return plain.decode("utf-8")


def _chrome_password(*, allow_prompt: bool = False) -> bytes:
    """Read this item only if already allowed; background polls never show dialogs."""
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    pointer = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = pointer
    cf.CFDictionaryCreate.argtypes = [pointer, pointer, pointer, ctypes.c_long, pointer, pointer]
    cf.CFDictionaryCreate.restype = pointer
    cf.CFRelease.argtypes = [pointer]
    cf.CFDataGetLength.argtypes = [pointer]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetBytePtr.argtypes = [pointer]
    cf.CFDataGetBytePtr.restype = pointer
    security.SecItemCopyMatching.argtypes = [pointer, ctypes.POINTER(pointer)]
    security.SecItemCopyMatching.restype = ctypes.c_int32

    def constant(name: str) -> int | None:
        return pointer.in_dll(security, name).value

    service = cf.CFStringCreateWithCString(None, b"Chrome Safe Storage", 0x08000100)
    account = cf.CFStringCreateWithCString(None, b"Chrome", 0x08000100)
    keys = (pointer * 5)(
        *[
            constant(k)
            for k in (
                "kSecClass",
                "kSecAttrService",
                "kSecAttrAccount",
                "kSecReturnData",
                "kSecUseAuthenticationUI",
            )
        ]
    )
    values = (pointer * 5)(
        constant("kSecClassGenericPassword"),
        service,
        account,
        pointer.in_dll(cf, "kCFBooleanTrue").value,
        constant("kSecUseAuthenticationUIAllow" if allow_prompt else "kSecUseAuthenticationUIFail"),
    )
    query = cf.CFDictionaryCreate(
        None,
        keys,
        values,
        5,
        ctypes.byref(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
        ctypes.byref(ctypes.c_byte.in_dll(cf, "kCFTypeDictionaryValueCallBacks")),
    )
    result = pointer()
    try:
        status = security.SecItemCopyMatching(query, ctypes.byref(result))
        if status != 0:
            raise MuseError(
                "Chrome Keychain access unavailable. Run llm-usage muse-connect to allow access, or set MUSE_COOKIE."
            )
        return ctypes.string_at(cf.CFDataGetBytePtr(result), cf.CFDataGetLength(result))
    finally:
        if result:
            cf.CFRelease(result)
        cf.CFRelease(query)
        cf.CFRelease(service)
        cf.CFRelease(account)


def chrome_cookie_headers(settings: Settings) -> list[str]:
    """Import only unexpired Muse sessions; never copy a browser database."""
    if not settings.muse_chrome_import or sys.platform != "darwin":
        return []
    root = settings.muse_chrome_dir
    profiles = [root / "Default", *sorted(root.glob("Profile *"))[:7]]
    rows: list[tuple[str, str, bytes, int]] = []
    chrome_now = int((time.time() + 11644473600) * 1_000_000)
    for profile in profiles:
        path = profile / "Cookies"
        if not path.is_file():
            path = profile / "Network" / "Cookies"
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.2) as db:
                version = int(
                    db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
                )
                for host, value, encrypted in db.execute(
                    "SELECT host_key,value,encrypted_value FROM cookies "
                    "WHERE host_key IN ('muse.ai','.muse.ai') AND name='hatch_sess' "
                    "AND (expires_utc=0 OR expires_utc>?) LIMIT 1",
                    (chrome_now,),
                ):
                    rows.append((host, value, encrypted, version))
        except (sqlite3.Error, ValueError, TypeError):
            continue
    if not rows:
        return []
    password: bytes | None = None
    if any(not row[1] for row in rows):
        password = _chrome_password()
    return [
        "hatch_sess=" + (value or _decrypt_cookie(encrypted, password or b"", host, version))
        for host, value, encrypted, version in rows
    ]


def _check_cookie(cookie: str) -> str:
    cookie = cookie.strip()
    if (
        not cookie
        or len(cookie) > 16384
        or any(ord(c) < 32 or ord(c) > 126 for c in cookie)
        or not any(
            part.strip().startswith("hatch_sess=") and part.strip() != "hatch_sess="
            for part in cookie.split(";")
        )
    ):
        raise MuseError("MUSE_COOKIE must be a muse.ai Cookie header containing hatch_sess.")
    return cookie


class _Website:
    def __init__(self, client: httpx.Client):
        self.client = client
        self.deadline = time.monotonic() + 25
        self.remaining = 16 * 1024 * 1024

    def request(
        self, method: str, path: str, cookie: str | None = None, action: str | None = None
    ) -> tuple[int, str]:
        # Callers supply root or a path extracted with _CHUNKS, never arbitrary URLs.
        if path != "/" and not re.fullmatch(r"/_next/static/chunks/[\w~.-]+\.js", path):
            raise MuseError("Invalid Muse asset path.")
        remaining_time = self.deadline - time.monotonic()
        if remaining_time <= 0 or self.remaining <= 0:
            raise MuseError("Muse discovery reached its time or size limit. Refresh to retry.")
        headers = {"User-Agent": _UA, "Accept": "text/html" if path == "/" else "*/*"}
        if cookie is not None:
            headers["Cookie"] = cookie
        else:
            # httpx also keeps Set-Cookie responses in its jar. Override it
            # explicitly so page cookies cannot reach static asset requests.
            headers["Cookie"] = ""
        if action is not None:
            headers.update(
                {
                    "Next-Action": action,
                    "Accept": "text/x-component",
                    "Origin": _ORIGIN,
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                }
            )
        with self.client.stream(
            method,
            _ORIGIN + path,
            headers=headers,
            json=[{"includeAgreement": False}] if action else None,
            timeout=min(6, remaining_time),
            follow_redirects=False,
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308, 401, 403):
                raise MuseError(
                    "Muse session expired or was rejected. Sign in at muse.ai in Chrome and refresh."
                )
            if response.status_code == 429:
                raise MuseError("Muse is rate limiting requests. Try again later.")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                self.remaining -= len(chunk)
                if (
                    len(body) > 4 * 1024 * 1024
                    or self.remaining < 0
                    or time.monotonic() > self.deadline
                ):
                    raise MuseError("Muse response exceeded the time or size limit.")
            return response.status_code, body.decode("utf-8", errors="replace")

    def discover(self, cookie: str) -> str:
        status, page = self.request("GET", "/", cookie)
        if status != 200:
            raise MuseError(f"Muse page returned HTTP {status}.")
        paths = list(dict.fromkeys(_CHUNKS.findall(page)))[:96]
        combined = ""
        # No cookies are sent to static assets. Batches bound concurrency and memory.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for offset in range(0, len(paths), 4):
                batch = list(
                    pool.map(
                        lambda p: self.request("GET", "/_next/" + p), paths[offset : offset + 4]
                    )
                )
                combined += "\n" + "\n".join(body for status, body in batch if status == 200)
                found = _REFERENCE.search(combined)
                if found:
                    return found[1]
                module = re.search(r"\.A\((\d+)\)\.then\(\(\{[^}]*Settings", combined)
                if not module:
                    continue
                loader = re.search(
                    r"[,{\[]" + module[1] + r",\w+=>\{\w+\.v\(\w+=>Promise\.all\(\[([^\]]*)",
                    combined,
                )
                if not loader:
                    continue
                settings_paths = list(dict.fromkeys(_CHUNKS.findall(loader[1])))[:32]
                for pos in range(0, len(settings_paths), 4):
                    assets = pool.map(
                        lambda p: self.request("GET", "/_next/" + p), settings_paths[pos : pos + 4]
                    )
                    for asset_status, body in assets:
                        match = _REFERENCE.search(body) if asset_status == 200 else None
                        if match:
                            return match[1]
                break
        raise MuseError("Muse settings action could not be found. The website may have changed.")

    def subscription(self, cookie: str) -> tuple[dict[str, Any], str]:
        cached = read_json_cache(_ACTION_CACHE, max_age_s=7 * 86400) or {}
        action = cached.get("action")
        if not isinstance(action, str) or not _ACTION_ID.fullmatch(action):
            action = self.discover(cookie)
        status, body = self.request("POST", "/", cookie, action)
        if status == 404 and "server action not found" in body.lower():
            write_json_cache(_ACTION_CACHE, {})
            action = self.discover(cookie)
            status, body = self.request("POST", "/", cookie, action)
        if status != 200:
            raise MuseError(f"Muse usage returned HTTP {status}. Refresh to retry.")
        for line in body.splitlines():
            match = re.fullmatch(r"[0-9a-f]+:(\{.*\})\s*", line)
            if not match:
                continue
            try:
                row = json.loads(match[1])
            except ValueError:
                continue
            if (
                isinstance(row, dict)
                and row.get("success") is True
                and isinstance(row.get("subscription"), dict)
            ):
                return row["subscription"], action
        raise MuseError("Unexpected Muse usage response. The website may have changed.")


def _apply_subscription(report: ProviderReport, sub: dict[str, Any]) -> None:
    usage = sub.get("usage")
    if not isinstance(usage, dict):
        raise MuseError("Muse did not report metered usage.")
    percent, reset = usage.get("percentUsed"), usage.get("resetsAt")
    if (
        isinstance(percent, bool)
        or not isinstance(percent, (int, float))
        or not math.isfinite(percent)
        or not 0 <= percent <= 100
    ):
        raise MuseError("Muse reported an invalid usage percentage.")
    if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset):
        raise MuseError("Muse did not report a valid reset time.")
    try:
        reset_iso = datetime.fromtimestamp(reset, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        raise MuseError("Muse reported an invalid reset time.") from None
    tier = sub.get("tier")
    plan = tier.get("name") if isinstance(tier, dict) else None
    plan = plan[:100] if isinstance(plan, str) else "Muse"
    window = {
        "key": "seven_day",
        "label": "Weekly",
        "used_percent": float(percent),
        "resets_at": reset_iso,
        "window_seconds": 7 * 86400,
        "estimate_pace": False,
    }
    report.meta["quota"] = {**window, "plan": plan, "windows": [window]}
    report.meta["plan_type"] = plan
    report.source = SourceKind.SUBSCRIPTION
    report.notes.append("Weekly allowance from muse.ai. Token totals and costs are not reported.")


def collect_muse(
    settings: Settings, start: date, end: date, *, quota_only: bool = False
) -> ProviderReport:
    report = ProviderReport(
        provider=ProviderId.MUSE,
        display_name="Muse",
        source=SourceKind.UNAVAILABLE,
        period_start=start,
        period_end=end,
        meta={"console_url": _ORIGIN},
    )
    try:
        cookies = (
            [_check_cookie(settings.muse_cookie)]
            if settings.muse_cookie
            else chrome_cookie_headers(settings)
        )
        if not cookies:
            report.notes.append("Sign in at muse.ai in Chrome on macOS, or set MUSE_COOKIE.")
            return report
        last_error: MuseError | None = None
        with httpx.Client(follow_redirects=False) as client:
            site = _Website(client)
            for cookie in cookies:
                try:
                    sub, action = site.subscription(_check_cookie(cookie))
                    _apply_subscription(report, sub)
                    write_json_cache(_ACTION_CACHE, {"action": action})
                    return report
                except MuseError as exc:
                    last_error = exc
            if last_error:
                raise last_error
    except MuseError as exc:
        report.errors.append(str(exc))
    except (httpx.HTTPError, OSError, ValueError):
        # Never include exceptions, response bodies, cookies or subprocess stderr.
        report.errors.append(
            "Muse connection or Chrome session access failed. Sign in at muse.ai and refresh."
        )
    return report
