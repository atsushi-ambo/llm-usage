"""Provider collectors."""

from __future__ import annotations

from datetime import date, timedelta

from llm_usage.config import Settings
from llm_usage.models import AggregateReport, ProviderReport
from llm_usage.providers.claude import collect_claude
from llm_usage.providers.codex import collect_codex
from llm_usage.providers.cursor import collect_cursor
from llm_usage.providers.gemini import collect_gemini
from llm_usage.providers.openai_provider import collect_openai
from llm_usage.providers.xai import collect_xai


def collect_all(settings: Settings, days: int | None = None) -> AggregateReport:
    """Run every provider collector and return a unified report."""
    window = days if days is not None else settings.days
    end = date.today()
    start = end - timedelta(days=max(window - 1, 0))

    reports: list[ProviderReport] = [
        collect_claude(settings, start, end),
        collect_openai(settings, start, end),
        collect_codex(settings, start, end),
        collect_xai(settings, start, end),
        collect_cursor(settings, start, end),
        collect_gemini(settings, start, end),
    ]

    return AggregateReport(period_start=start, period_end=end, providers=reports)


__all__ = ["collect_all"]
