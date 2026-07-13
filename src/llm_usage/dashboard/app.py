"""Local FastAPI dashboard for LLM usage."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from llm_usage import __version__
from llm_usage.config import load_settings
from llm_usage.providers import collect_all

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="llm-usage", version=__version__)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/api/usage")
def api_usage(days: int | None = Query(default=None, ge=1, le=365)) -> JSONResponse:
    settings = load_settings()
    report = collect_all(settings, days=days)
    return JSONResponse(report.model_dump(mode="json"))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>llm-usage</h1><p>Missing static/index.html</p>")
