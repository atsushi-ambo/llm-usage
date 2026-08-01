"""collect_all fans out collectors; merge still waits for both OpenAI sides."""

from __future__ import annotations

import threading
import time
from datetime import date
from unittest.mock import patch

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.providers import collect_all


def _stub(name: str, delay: float = 0.05, **kwargs) -> ProviderReport:
    time.sleep(delay)
    return ProviderReport(
        provider=ProviderId(name) if name in ProviderId._value2member_map_ else ProviderId.LOCAL,
        display_name=name,
        source=SourceKind.UNAVAILABLE,
        period_start=date.today(),
        period_end=date.today(),
        meta={"started": True},
    )


def test_collect_all_runs_collectors_concurrently():
    """Wall-clock should be ~max delay, not sum — 6 × 0.15s sequential would
    be ~0.9s; concurrent stays under ~0.5s with headroom for CI."""
    started: list[float] = []
    lock = threading.Lock()

    def slow_collect(*_a, **_k):
        with lock:
            started.append(time.monotonic())
        time.sleep(0.12)
        return ProviderReport(
            provider=ProviderId.CLAUDE,
            display_name="stub",
            source=SourceKind.UNAVAILABLE,
            period_start=date.today(),
            period_end=date.today(),
        )

    settings = Settings()
    t0 = time.monotonic()
    with (
        patch("llm_usage.providers.collect_claude", side_effect=slow_collect),
        patch("llm_usage.providers.collect_codex", side_effect=slow_collect),
        patch("llm_usage.providers.collect_openai", side_effect=slow_collect),
        patch("llm_usage.providers.collect_xai", side_effect=slow_collect),
        patch("llm_usage.providers.collect_cursor", side_effect=slow_collect),
        patch("llm_usage.providers.collect_gemini", side_effect=slow_collect),
        patch("llm_usage.providers.collect_openrouter", side_effect=slow_collect),
        patch("llm_usage.providers.collect_cohere", side_effect=slow_collect),
        patch("llm_usage.providers.collect_mistral", side_effect=slow_collect),
        patch("llm_usage.providers.collect_replicate", side_effect=slow_collect),
        patch("llm_usage.providers.collect_huggingface", side_effect=slow_collect),
        patch("llm_usage.providers.get_custom_providers", return_value=[]),
        patch("llm_usage.logcache.prune_missing_sources", return_value=0),
    ):
        report = collect_all(settings, days=7)
    elapsed = time.monotonic() - t0

    # 10 cards: claude + merged openai/codex + xai + cursor + gemini + openrouter
    # + cohere + mistral + replicate + huggingface
    assert len(report.providers) == 10
    # 11 collectors start near-simultaneously (openai + codex both run; merge after).
    assert len(started) == 11
    span = max(started) - min(started)
    assert span < 0.1, f"starts were staggered over {span:.3f}s — looks sequential"
    # Sequential would be ~11 × 0.12 ≈ 1.3s; concurrent ≈ 0.12s + overhead.
    assert elapsed < 0.55, f"collect_all took {elapsed:.3f}s; expected concurrent ~0.12s"
