"""Replicate usage collector (placeholder until billing is fully wired)."""

from __future__ import annotations

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def collect(settings: Settings) -> ProviderReport:
    """Return Replicate usage if configured.

    Replicate's billing surface is account-specific and not stable enough for
    reliable aggregation here yet; we only report configuration state.
    """
    if not settings.replicate_api_key:
        return ProviderReport(
            provider=ProviderId.REPLICATE,
            display_name="Replicate",
            source=SourceKind.UNAVAILABLE,
            notes=["Set REPLICATE_API_KEY to enable this provider."],
            meta={"console_url": "https://replicate.com/account/billing"},
        )

    return ProviderReport(
        provider=ProviderId.REPLICATE,
        display_name="Replicate",
        source=SourceKind.UNAVAILABLE,
        notes=[
            "REPLICATE_API_KEY is set, but detailed usage aggregation is not "
            "implemented yet. Check Replicate billing for spend."
        ],
        meta={"console_url": "https://replicate.com/account/billing"},
    )
