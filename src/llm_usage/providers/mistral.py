"""Mistral AI usage collector (placeholder until a public usage API exists)."""

from __future__ import annotations

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def collect(settings: Settings) -> ProviderReport:
    """Return Mistral usage if configured.

    Mistral does not currently expose a public usage/billing API suitable for
    aggregation, so this only reports configuration state.
    """
    if not settings.mistral_api_key:
        return ProviderReport(
            provider=ProviderId.MISTRAL,
            display_name="Mistral AI",
            source=SourceKind.UNAVAILABLE,
            notes=["Set MISTRAL_API_KEY to enable this provider."],
            meta={"console_url": "https://console.mistral.ai/"},
        )

    return ProviderReport(
        provider=ProviderId.MISTRAL,
        display_name="Mistral AI",
        source=SourceKind.UNAVAILABLE,
        notes=[
            "MISTRAL_API_KEY is set, but Mistral has no public usage API yet. "
            "Check the Mistral console for spend."
        ],
        meta={"console_url": "https://console.mistral.ai/"},
    )
