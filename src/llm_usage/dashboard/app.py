"""Local FastAPI dashboard for LLM usage."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from llm_usage import __version__
from llm_usage.config import Settings, load_settings
from llm_usage.providers import collect_all_cached
from llm_usage.quota import write_dashboard_session
from llm_usage.serialize import report_to_dict

STATIC_DIR = Path(__file__).parent / "static"

COOKIE_NAME = "llm_usage_token"
# Paths that don't need the token: health is unauthenticated so the menubar's
# liveness probe keeps working, and neither leaks anything sensitive.
_OPEN_PATHS = {"/api/health"}


def _hostname_only(host_header: str) -> str:
    host_header = host_header.strip()
    if host_header.startswith("["):
        # IPv6 literal, e.g. "[::1]:8765" or "[::1]"
        return host_header.split("]")[0].lstrip("[")
    if host_header.count(":") == 1:
        return host_header.split(":", 1)[0]
    return host_header


class LoopbackHostMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Host header isn't a loopback name.

    This is the actual defense against DNS-rebinding: an attacker page can
    only ever present a Host header matching a domain it controls, never
    "127.0.0.1"/"localhost", so a rebound request is rejected here before it
    reaches any route.
    """

    ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "testserver"}

    async def dispatch(self, request: Request, call_next):
        host = _hostname_only(request.headers.get("host", ""))
        if host not in self.ALLOWED_HOSTNAMES:
            return JSONResponse({"error": "Host not allowed"}, status_code=400)
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """Require the startup-generated token (query param once, then a cookie).

    Mirrors the Jupyter-style local-tool pattern: the token is only ever
    printed to the terminal that started the server, so another local user
    or a stray webpage on localhost can't discover it by simply requesting
    "/" — every path (other than the harmless health check) is gated.
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN_PATHS:
            return await call_next(request)

        supplied = request.query_params.get("token")
        cookie_token = request.cookies.get(COOKIE_NAME)
        supplied_ok = bool(supplied) and secrets.compare_digest(supplied, self._token)
        cookie_ok = bool(cookie_token) and secrets.compare_digest(cookie_token, self._token)

        if not (supplied_ok or cookie_ok):
            return JSONResponse(
                {
                    "error": "Missing or invalid token. Open the URL printed by "
                    "`llm-usage dashboard` (it includes ?token=...)."
                },
                status_code=403,
            )

        response = await call_next(request)
        if supplied_ok and supplied != cookie_token:
            response.set_cookie(
                COOKIE_NAME,
                self._token,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return response


def _security_headers(response: Response) -> None:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    token = secrets.token_urlsafe(24)

    app = FastAPI(title="llm-usage", version=__version__)
    app.state.settings = settings
    app.state.token = token

    try:
        write_dashboard_session(token, settings.host, settings.port)
    except Exception:  # noqa: BLE001
        pass  # menubar's "Open Dashboard" falls back to spawning + no token

    # Last-added middleware runs outermost (closest to the client), so the
    # Host check happens before the token check.
    app.add_middleware(AuthMiddleware, token=token)
    app.add_middleware(LoopbackHostMiddleware)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        _security_headers(response)
        return response

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/usage")
    def api_usage(
        days: int | None = Query(default=None, ge=1, le=365),
        refresh: bool = Query(default=False),
    ) -> JSONResponse:
        # The disk-backed snapshot (see collect_all_cached) is shared with
        # the CLI and menubar, so a request landing right after either of
        # those already reuses their result instead of re-hitting every
        # provider API. ?refresh=1 (the dashboard's "Refresh" button) forces
        # a fresh collection.
        report = collect_all_cached(app.state.settings, days, force_refresh=refresh)
        return JSONResponse(report_to_dict(report))

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html_path = STATIC_DIR / "index.html"
        if not html_path.exists():
            return HTMLResponse("<h1>llm-usage</h1><p>Missing static/index.html</p>")
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    return app


app = create_app()
