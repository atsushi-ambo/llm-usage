"""Provider collectors."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from llm_usage.config import Settings
from llm_usage.models import AggregateReport, ModelUsage, ProviderId, ProviderReport, SourceKind
from llm_usage.providers.claude import collect_claude
from llm_usage.providers.codex import collect_codex
from llm_usage.providers.cursor import collect_cursor
from llm_usage.providers.gemini import collect_gemini
from llm_usage.providers.openai_provider import collect_openai
from llm_usage.providers.openrouter import collect_openrouter
from llm_usage.providers.xai import collect_xai
from llm_usage.quota import read_json_cache, write_json_cache
from llm_usage.serialize import report_to_dict

# Most provider collectors have no caching of their own (Claude's OAuth quota
# is the one exception — see quota.py) and every collect_all() call hits
# every configured provider API unconditionally. Multiple frontends can run
# close together (a dashboard page load right after a menubar poll, a CLI
# invocation while the dashboard is open) and each used to trigger its own
# independent round of live API calls. This is the default TTL for
# collect_all_cached()'s disk-backed snapshot, which lets near-simultaneous
# callers share one collection instead.
DEFAULT_SNAPSHOT_TTL_S = 90.0

# Collectors are independent HTTP/log work; wall-clock ≈ slowest, not sum.
# 7 = claude, codex, openai, xai, cursor, gemini, openrouter (quota_only skips openai).
_COLLECT_WORKERS = 7


def collect_all(
    settings: Settings,
    days: int | None = None,
    *,
    quota_only: bool = False,
) -> AggregateReport:
    """Run every provider collector and return a unified report.

    quota_only=True (menubar): skip local log scans, org usage time-series,
    and model-list calls. Only fetch subscription / credit bars so the
    long-running menubar process stays light on RAM and CPU.
    """
    window = days if days is not None else settings.days
    end = date.today()
    start = end - timedelta(days=max(window - 1, 0))

    if not quota_only:
        # Drop logscan cache entries for rotated/deleted session files so
        # ~/.config/llm-usage/cache/logscan/ doesn't grow without bound.
        try:
            from llm_usage.logcache import prune_missing_sources

            prune_missing_sources()
        except Exception:  # noqa: BLE001
            pass

    with ThreadPoolExecutor(max_workers=_COLLECT_WORKERS) as pool:
        fut_claude = pool.submit(
            collect_claude, settings, start, end, quota_only=quota_only
        )
        fut_codex = pool.submit(collect_codex, settings, start, end, quota_only=quota_only)
        fut_openai = (
            None
            if quota_only
            else pool.submit(collect_openai, settings, start, end)
        )
        fut_xai = pool.submit(collect_xai, settings, start, end, quota_only=quota_only)
        fut_cursor = pool.submit(
            collect_cursor, settings, start, end, quota_only=quota_only
        )
        fut_gemini = pool.submit(
            collect_gemini, settings, start, end, quota_only=quota_only
        )
        fut_openrouter = pool.submit(
            collect_openrouter, settings, start, end, quota_only=quota_only
        )

        claude = fut_claude.result()
        codex = fut_codex.result()
        if fut_openai is None:
            # Platform API org usage is heavy and not shown as a menu bar % —
            # Codex live quota alone is enough for the OpenAI family row.
            openai = ProviderReport(
                provider=ProviderId.OPENAI,
                display_name="OpenAI",
                source=SourceKind.UNAVAILABLE,
                period_start=start,
                period_end=end,
            )
        else:
            openai = fut_openai.result()
        xai = fut_xai.result()
        cursor = fut_cursor.result()
        gemini = fut_gemini.result()
        openrouter = fut_openrouter.result()

    openai_family = _merge_openai_family(codex, openai)
    reports: list[ProviderReport] = [
        claude,
        openai_family,
        xai,
        cursor,
        gemini,
        openrouter,
    ]

    return AggregateReport(period_start=start, period_end=end, providers=reports)


def collect_all_cached(
    settings: Settings,
    days: int | None = None,
    *,
    ttl_s: float = DEFAULT_SNAPSHOT_TTL_S,
    force_refresh: bool = False,
    quota_only: bool = False,
) -> AggregateReport:
    """collect_all(), reused across processes/frontends for `ttl_s` seconds
    via a disk-backed snapshot (~/.config/llm-usage/cache). CLI, dashboard,
    and menubar invocations that land within the same window share one
    collection instead of each independently re-hitting every provider API.

    Keyed by the resolved --days window and quota_only flag so the menubar's
    light snapshot never collides with a full CLI/dashboard report.
    """
    window = days if days is not None else settings.days
    cache_name = (
        f"report_snapshot_quota_{window}.json"
        if quota_only
        else f"report_snapshot_{window}.json"
    )

    if not force_refresh and ttl_s > 0:
        cached = read_json_cache(cache_name, max_age_s=ttl_s)
        if cached is not None:
            try:
                return AggregateReport.model_validate(cached)
            except Exception:  # noqa: BLE001
                pass  # corrupt/incompatible cache entry — fall through

    report = collect_all(settings, days=days, quota_only=quota_only)
    if ttl_s > 0:
        # Strip raw upstream payloads before disk — smaller cache, less RAM
        # when reloading, and avoids parking OAuth bodies on disk.
        write_json_cache(cache_name, report_to_dict(report, include_raw_meta=False))
    return report


def _merge_openai_family(codex: ProviderReport, openai: ProviderReport) -> ProviderReport:
    """Combine ChatGPT/Codex plan usage with optional Platform API billing.

    "Has data" here deliberately means "source got set past UNAVAILABLE (or
    we can see tokens/quota)" rather than "no errors occurred" — a collector
    that fetched partial data before hitting an error still has something
    worth showing, and its errors are preserved via report.errors either way
    (see the "both have data" branch below and each collect_* function).
    """
    has_codex = codex.source != SourceKind.UNAVAILABLE or codex.requests > 0 or codex.meta.get(
        "quota"
    )
    has_api = openai.source != SourceKind.UNAVAILABLE or (openai.cost_usd is not None)

    if not has_api and has_codex:
        codex.display_name = "OpenAI / Codex"
        codex.provider = ProviderId.CODEX
        # openai.errors here would only be real errors (has_api is False,
        # but that doesn't mean openai.collect() didn't hit one) — surface
        # them instead of silently dropping the unreturned report.
        codex.errors.extend(f"API: {e}" for e in openai.errors)
        return codex
    if has_api and not has_codex:
        openai.display_name = "OpenAI / Codex"
        openai.provider = ProviderId.OPENAI
        openai.notes = [
            "Platform API usage only (no Codex ChatGPT plan data found).",
            *openai.notes,
        ]
        openai.errors.extend(codex.errors)
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
        codex.errors.extend(f"API: {e}" for e in openai.errors)
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


__all__ = ["collect_all", "collect_all_cached"]
