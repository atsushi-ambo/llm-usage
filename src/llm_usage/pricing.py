"""Approximate per-model pricing (USD per 1M tokens).

Used to estimate cost from local logs when billing APIs are unavailable.
Prices are approximate snapshots — treat as estimates, not invoices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_m: float
    output_per_m: float
    cache_read_per_m: float | None = None
    cache_write_per_m: float | None = None


# Keys are lower-case substrings matched against model ids (longest match wins).
PRICES: dict[str, ModelPrice] = {
    # Anthropic (longest substring match wins)
    "claude-opus-4": ModelPrice(15.0, 75.0, 1.50, 18.75),
    "claude-sonnet-4": ModelPrice(3.0, 15.0, 0.30, 3.75),
    "claude-sonnet-5": ModelPrice(3.0, 15.0, 0.30, 3.75),
    "claude-haiku-4": ModelPrice(1.0, 5.0, 0.10, 1.25),
    "claude-3-5-sonnet": ModelPrice(3.0, 15.0, 0.30, 3.75),
    "claude-3-5-haiku": ModelPrice(0.80, 4.0, 0.08, 1.0),
    "claude-3-opus": ModelPrice(15.0, 75.0, 1.50, 18.75),
    "claude-3-sonnet": ModelPrice(3.0, 15.0),
    "claude-3-haiku": ModelPrice(0.25, 1.25),
    "claude-sonnet": ModelPrice(3.0, 15.0, 0.30, 3.75),
    "claude-opus": ModelPrice(15.0, 75.0, 1.50, 18.75),
    "claude-haiku": ModelPrice(1.0, 5.0, 0.10, 1.25),
    # OpenAI
    "gpt-4.1": ModelPrice(2.0, 8.0),
    "gpt-4o": ModelPrice(2.50, 10.0),
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    "o3": ModelPrice(10.0, 40.0),
    "o4-mini": ModelPrice(1.10, 4.40),
    "o1": ModelPrice(15.0, 60.0),
    "gpt-4-turbo": ModelPrice(10.0, 30.0),
    "gpt-3.5-turbo": ModelPrice(0.50, 1.50),
    # xAI / Grok
    "grok-4.5": ModelPrice(3.0, 15.0),
    "grok-4": ModelPrice(3.0, 15.0),
    "grok-3": ModelPrice(3.0, 15.0),
    "grok-2": ModelPrice(2.0, 10.0),
    "grok-code": ModelPrice(0.20, 1.50),
    # Codex / OpenAI coding models
    "gpt-5": ModelPrice(1.25, 10.0),
    "codex": ModelPrice(1.25, 10.0),
    # Gemini
    "gemini-2.5-pro": ModelPrice(1.25, 10.0),
    "gemini-2.5-flash": ModelPrice(0.15, 0.60),
    "gemini-2.0-flash": ModelPrice(0.10, 0.40),
    "gemini-1.5-pro": ModelPrice(1.25, 5.0),
    "gemini-1.5-flash": ModelPrice(0.075, 0.30),
}


# Word-boundary matching (not bare substring) so a short key like "o1" or
# "codex" only matches a whole id segment — e.g. it won't fire on a "...4o1..."
# or "...codex..." that happens to appear mid-token with no separator.
# Hyphens/dots count as separators since \b triggers on any non-word char.
_PRICE_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(r"\b" + re.escape(name) + r"\b") for name in PRICES
}


def lookup_price(model: str) -> ModelPrice | None:
    key = (model or "").lower()
    if not key or key == "<synthetic>":
        return None
    best: tuple[int, ModelPrice] | None = None
    for name, pattern in _PRICE_PATTERNS.items():
        if pattern.search(key):
            if best is None or len(name) > best[0]:
                best = (len(name), PRICES[name])
    return best[1] if best else None


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    price = lookup_price(model)
    if price is None:
        return None
    cost = (input_tokens / 1_000_000) * price.input_per_m
    cost += (output_tokens / 1_000_000) * price.output_per_m
    if cache_read_tokens and price.cache_read_per_m is not None:
        cost += (cache_read_tokens / 1_000_000) * price.cache_read_per_m
    if cache_write_tokens and price.cache_write_per_m is not None:
        cost += (cache_write_tokens / 1_000_000) * price.cache_write_per_m
    return cost
