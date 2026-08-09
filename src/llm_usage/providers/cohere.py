"""Cohere usage collector (placeholder until a public usage API exists)."""

from __future__ import annotations

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def collect(settings: Settings) -> ProviderReport:
    """Return Cohere usage if configured.

    Cohere does not currently expose a public usage/billing API suitable for
    aggregation, so this only reports configuration state.
    """
    if not settings.cohere_api_key:
        return ProviderReport(
            provider=ProviderId.COHERE,
            display_name="Cohere",
            source=SourceKind.UNAVAILABLE,
            notes=["Set COHERE_API_KEY to enable this provider."],
            meta={"console_url": "https://dashboard.cohere.com/"},
        )

    return ProviderReport(
        provider=ProviderId.COHERE,
        display_name="Cohere",
        source=SourceKind.UNAVAILABLE,
        notes=[
            "COHERE_API_KEY is set, but Cohere has no public usage API yet. "
            "Check the Cohere dashboard for spend."
        ],
        meta={"console_url": "https://dashboard.cohere.com/"},
    )
