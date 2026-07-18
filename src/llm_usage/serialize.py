"""Shared, privacy-conscious serialization for AggregateReport."""

from __future__ import annotations

from typing import Any

from llm_usage.models import AggregateReport

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
