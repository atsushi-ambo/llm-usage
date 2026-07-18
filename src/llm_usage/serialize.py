"""Shared, privacy-conscious serialization for AggregateReport."""

from __future__ import annotations

from typing import Any

from llm_usage.models import AggregateReport, ProviderReport

# Keys in ProviderReport.meta that hold raw upstream payloads (full OAuth
# usage bodies, billing snapshots, API-key listings, etc). Useful for
# debugging, too sensitive/verbose for default API/export output.
RAW_META_KEYS = {
    "subscription",
    "spend",
    "raw_dashboard",
    "api_keys",
    "available_models",
    # Past-period billing snapshot kept for diagnostics; still vendor-raw.
    "stale_billing",
}

# Meta keys the menubar actually reads for bars / labels.
_MENUBAR_META_KEYS = frozenset(
    {"quota", "plan_type", "console_url", "billing_source"}
)
_QUOTA_KEYS = frozenset(
    {"used_percent", "label", "plan", "resets_at", "windows", "period_start", "period_type"}
)
_WINDOW_KEYS = frozenset({"key", "label", "used_percent", "resets_at"})


def report_to_dict(report: AggregateReport, *, include_raw_meta: bool = False) -> dict[str, Any]:
    """Serialize a report, stripping verbatim upstream payloads by default."""
    data = report.model_dump(mode="json")
    if not include_raw_meta:
        for provider in data.get("providers", []):
            meta = provider.get("meta")
            if isinstance(meta, dict):
                for key in RAW_META_KEYS:
                    meta.pop(key, None)
    return data


def slim_report_for_menubar(report: AggregateReport) -> AggregateReport:
    """Drop heavy fields the menu bar never renders.

    Full collection still builds models/daily/raw meta briefly, but the
    long-lived process should not hold 30 days of per-model history in RAM
    between polls. Keeps: identity, aggregates, compact quota windows.
    """
    slim_providers: list[ProviderReport] = []
    for p in report.providers:
        meta_in = p.meta or {}
        slim_meta: dict[str, Any] = {}
        for key in _MENUBAR_META_KEYS:
            if key not in meta_in:
                continue
            val = meta_in[key]
            if key == "quota" and isinstance(val, dict):
                q = {k: val[k] for k in _QUOTA_KEYS if k in val}
                windows = q.get("windows")
                if isinstance(windows, list):
                    q["windows"] = [
                        {k: w.get(k) for k in _WINDOW_KEYS if k in w}
                        for w in windows
                        if isinstance(w, dict)
                    ]
                slim_meta["quota"] = q
            else:
                slim_meta[key] = val

        slim_providers.append(
            ProviderReport(
                provider=p.provider,
                display_name=p.display_name,
                source=p.source,
                period_start=p.period_start,
                period_end=p.period_end,
                input_tokens=p.input_tokens,
                output_tokens=p.output_tokens,
                cache_read_tokens=p.cache_read_tokens,
                cache_write_tokens=p.cache_write_tokens,
                requests=p.requests,
                cost_usd=p.cost_usd,
                currency=p.currency,
                models=[],  # never shown in menubar
                daily=[],  # never shown in menubar
                meta=slim_meta,
                errors=(p.errors or [])[:2],
                notes=[],  # never shown in menubar
                fetched_at=p.fetched_at,
            )
        )
    return AggregateReport(
        period_start=report.period_start,
        period_end=report.period_end,
        providers=slim_providers,
        generated_at=report.generated_at,
    )
