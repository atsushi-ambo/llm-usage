"""Dashboard security boundary + API redaction tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from llm_usage.config import Settings
from llm_usage.dashboard.app import create_app
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind
from llm_usage.serialize import report_to_dict


def _settings() -> Settings:
    # Minimal settings — no real keys, isolated via conftest cache dir.
    return Settings(days=7, host="127.0.0.1", port=8765)


def _sample_report() -> AggregateReport:
    return AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude Code",
                source=SourceKind.SUBSCRIPTION,
                cost_usd=1.5,
                meta={
                    "estimated": True,
                    "quota": {"used_percent": 12.0, "label": "5-hour"},
                    # Must be stripped by report_to_dict /api/usage
                    "subscription": {"access_token": "SECRET_SHOULD_NOT_LEAK"},
                    "spend": {"raw": "billing"},
                },
            )
        ],
    )


def test_create_app_not_imported_as_module_app():
    import llm_usage.dashboard.app as mod

    assert not hasattr(mod, "app") or getattr(mod, "app", None) is None
    assert callable(mod.create_app)


def test_health_open_without_token():
    app = create_app(_settings())
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_index_requires_token():
    app = create_app(_settings())
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 403


def test_index_accepts_token_query():
    app = create_app(_settings())
    client = TestClient(app)
    r = client.get("/", params={"token": app.state.token})
    assert r.status_code == 200
    assert "llm-usage" in r.text.lower() or "Overview" in r.text


def test_wrong_token_is_403():
    app = create_app(_settings())
    client = TestClient(app)
    r = client.get("/api/usage", params={"token": "not-the-real-token"})
    assert r.status_code == 403


def test_loopback_host_middleware_rejects_foreign_host():
    app = create_app(_settings())
    client = TestClient(app)
    r = client.get(
        "/api/health",
        headers={"Host": "evil.example"},
    )
    assert r.status_code == 400
    assert "Host" in r.json().get("error", "")


def test_api_usage_redacts_raw_meta():
    app = create_app(_settings())
    client = TestClient(app)
    report = _sample_report()
    with patch(
        "llm_usage.dashboard.app.collect_all_cached",
        return_value=report,
    ):
        r = client.get(
            "/api/usage",
            params={"token": app.state.token, "days": 7},
        )
    assert r.status_code == 200
    body = r.json()
    meta = body["providers"][0]["meta"]
    assert "subscription" not in meta
    assert "spend" not in meta
    assert meta.get("estimated") is True
    assert meta.get("quota", {}).get("used_percent") == 12.0
    assert body.get("has_estimated_cost") is True
    assert body.get("estimated_cost_usd") == 1.5
    assert body.get("billed_cost_usd") is None
    assert "prices_as_of" in body


def test_report_to_dict_redaction_unit():
    data = report_to_dict(_sample_report())
    meta = data["providers"][0]["meta"]
    assert "subscription" not in meta
    assert "spend" not in meta
    assert data["estimated_cost_usd"] == 1.5
