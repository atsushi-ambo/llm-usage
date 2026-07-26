"""CLI surface tests (exit codes, --format json for scriptable commands)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from typer.testing import CliRunner

from llm_usage.cli import app
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind

runner = CliRunner()


def _empty_report() -> AggregateReport:
    return AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude Code",
                source=SourceKind.UNAVAILABLE,
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 7),
            ),
            ProviderReport(
                provider=ProviderId.CODEX,
                display_name="OpenAI / Codex",
                source=SourceKind.UNAVAILABLE,
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 7),
            ),
        ],
    )


def _report_with_quota(*, used_percent: float) -> AggregateReport:
    return AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude Code",
                source=SourceKind.SUBSCRIPTION,
                meta={
                    "quota": {
                        "used_percent": used_percent,
                        "label": "5-hour limit",
                        "windows": [
                            {
                                "key": "five_hour",
                                "label": "5-hour",
                                "used_percent": used_percent,
                            }
                        ],
                    }
                },
            )
        ],
    )


def test_version_exits_zero():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "llm-usage" in result.stdout


def test_status_exits_zero():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Claude" in result.stdout


def test_check_ok_exit_zero_json():
    report = _report_with_quota(used_percent=40.0)
    with patch("llm_usage.cli.collect_all_cached", return_value=report) as mock_collect:
        result = runner.invoke(app, ["check", "--format", "json"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout or '"ok":true' in result.stdout
    # Cron path must use quota_only so it skips local log scans.
    kwargs = mock_collect.call_args.kwargs
    assert kwargs.get("quota_only") is True


def test_check_breach_exit_one_json():
    report = _report_with_quota(used_percent=95.0)
    with patch("llm_usage.cli.collect_all_cached", return_value=report):
        result = runner.invoke(app, ["check", "--format", "json", "--fail-at", "90"])
    assert result.exit_code == 1
    assert "breached" in result.stdout
    assert "95" in result.stdout


def test_doctor_ok_json():
    report = _empty_report()
    # mark one configured + healthy
    report.providers[0] = ProviderReport(
        provider=ProviderId.CLAUDE,
        display_name="Claude Code",
        source=SourceKind.LOCAL_LOGS,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        requests=1,
    )
    with patch("llm_usage.cli.collect_all_cached", return_value=report):
        result = runner.invoke(app, ["doctor", "--format", "json"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout or '"ok":true' in result.stdout


def test_doctor_error_exit_one_json():
    report = AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 7),
        providers=[
            ProviderReport(
                provider=ProviderId.CLAUDE,
                display_name="Claude Code",
                source=SourceKind.UNAVAILABLE,
                errors=["OAuth token expired"],
            )
        ],
    )
    with patch("llm_usage.cli.collect_all_cached", return_value=report):
        result = runner.invoke(app, ["doctor", "--format", "json"])
    assert result.exit_code == 1
    assert "error" in result.stdout
