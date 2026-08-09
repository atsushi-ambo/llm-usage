"""Hugging Face usage collector (placeholder until a public usage API exists)."""

from __future__ import annotations

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind


def collect(settings: Settings) -> ProviderReport:
    """Return Hugging Face usage if configured.

    Hugging Face does not currently expose a comprehensive public usage API
    for Inference spend, so this only reports configuration state.
    """
    if not settings.huggingface_api_key:
        return ProviderReport(
            provider=ProviderId.HUGGINGFACE,
            display_name="Hugging Face",
            source=SourceKind.UNAVAILABLE,
            notes=["Set HUGGINGFACE_API_KEY to enable this provider."],
            meta={"console_url": "https://huggingface.co/settings/billing"},
        )

    return ProviderReport(
        provider=ProviderId.HUGGINGFACE,
        display_name="Hugging Face",
        source=SourceKind.UNAVAILABLE,
        notes=[
            "HUGGINGFACE_API_KEY is set, but Hugging Face has no comprehensive "
            "public usage API yet. Check HF billing for spend."
        ],
        meta={"console_url": "https://huggingface.co/settings/billing"},
    )
