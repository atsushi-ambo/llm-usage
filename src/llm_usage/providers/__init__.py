"""Provider collectors."""

from __future__ import annotations

from datetime import date, timedelta

from llm_usage.config import Settings
from llm_usage.models import AggregateReport, ModelUsage, ProviderId, ProviderReport, SourceKind
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

    claude = collect_claude(settings, start, end)
    openai = collect_openai(settings, start, end)
    codex = collect_codex(settings, start, end)
    # Merge Platform API (OpenAI) into Codex/ChatGPT plan card — one OpenAI family row
    openai_family = _merge_openai_family(codex, openai)

    reports: list[ProviderReport] = [
        claude,
        openai_family,
        collect_xai(settings, start, end),
        collect_cursor(settings, start, end),
        collect_gemini(settings, start, end),
    ]

    return AggregateReport(period_start=start, period_end=end, providers=reports)


def _merge_openai_family(codex: ProviderReport, openai: ProviderReport) -> ProviderReport:
    """Combine ChatGPT/Codex plan usage with optional Platform API billing."""
    has_codex = codex.source != SourceKind.UNAVAILABLE or codex.requests > 0 or codex.meta.get(
        "quota"
    )
    has_api = openai.source != SourceKind.UNAVAILABLE or (openai.cost_usd is not None)

    if not has_api and has_codex:
        codex.display_name = "OpenAI / Codex"
        codex.provider = ProviderId.CODEX
        return codex
    if has_api and not has_codex:
        openai.display_name = "OpenAI / Codex"
        openai.provider = ProviderId.OPENAI
        openai.notes = [
            "Platform API usage only (no Codex ChatGPT plan data found).",
            *openai.notes,
        ]
        return openai
    if not has_api and not has_codex:
        # Prefer a single empty card
        codex.display_name = "OpenAI / Codex"
        codex.notes = [
            "No Codex plan usage and no Platform API keys. "
            "Use Codex (ChatGPT login) and/or set OPENAI_ADMIN_KEY."
        ]
        # merge notes from openai setup hints
        for n in openai.notes:
            if n not in codex.notes:
                codex.notes.append(n)
        return codex

    # Both have data: prefer Codex as base (subscription) + attach API cost/tokens
    merged = codex.model_copy(deep=True)
    merged.display_name = "OpenAI / Codex"
    merged.provider = ProviderId.CODEX

    # Sum tokens/requests where both present
    merged.input_tokens = (codex.input_tokens or 0) + (openai.input_tokens or 0)
    merged.output_tokens = (codex.output_tokens or 0) + (openai.output_tokens or 0)
    merged.cache_read_tokens = (codex.cache_read_tokens or 0) + (
        openai.cache_read_tokens or 0
    )
    merged.requests = (codex.requests or 0) + (openai.requests or 0)

    # Cost: Platform API is real $; Codex plan is quota (None)
    if openai.cost_usd is not None:
        merged.cost_usd = openai.cost_usd
        merged.meta["api_cost_usd"] = openai.cost_usd
        merged.meta["estimated"] = openai.meta.get("estimated", False)

    # Combine models (prefix source)
    models: list[ModelUsage] = []
    for m in codex.models:
        models.append(
            ModelUsage(
                model=f"[plan] {m.model}",
                input_tokens=m.input_tokens,
                output_tokens=m.output_tokens,
                cache_read_tokens=m.cache_read_tokens,
                cache_write_tokens=m.cache_write_tokens,
                requests=m.requests,
                cost_usd=m.cost_usd,
            )
        )
    for m in openai.models:
        models.append(
            ModelUsage(
                model=f"[api] {m.model}",
                input_tokens=m.input_tokens,
                output_tokens=m.output_tokens,
                cache_read_tokens=m.cache_read_tokens,
                cache_write_tokens=m.cache_write_tokens,
                requests=m.requests,
                cost_usd=m.cost_usd,
            )
        )
    if models:
        merged.models = models

    merged.meta["openai_api"] = {
        "source": openai.source.value,
        "cost_usd": openai.cost_usd,
        "requests": openai.requests,
        "total_tokens": openai.total_tokens,
    }
    # Keep codex quota as the primary progress bar
    if openai.notes:
        merged.notes.append("Platform API: " + openai.notes[0])
    merged.notes.append(
        "Merged ChatGPT/Codex plan + OpenAI Platform API into one card."
    )
    if codex.errors:
        merged.errors.extend(codex.errors)
    if openai.errors:
        merged.errors.extend([f"API: {e}" for e in openai.errors])

    # Prefer richer source label
    if codex.source != SourceKind.UNAVAILABLE:
        merged.source = codex.source
    elif openai.source != SourceKind.UNAVAILABLE:
        merged.source = openai.source

    return merged


__all__ = ["collect_all"]
