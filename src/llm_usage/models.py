"""Shared data models for usage aggregation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderId(str, Enum):
    CLAUDE = "claude"
    OPENAI = "openai"
    CODEX = "codex"
    GROK = "grok"
    CURSOR = "cursor"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    LOCAL = "local"


class SourceKind(str, Enum):
    """Where the numbers came from."""

    API = "api"
    LOCAL_LOGS = "local_logs"
    SUBSCRIPTION = "subscription"
    MANUAL = "manual"
    UNAVAILABLE = "unavailable"


class ModelUsage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


class DailyPoint(BaseModel):
    day: date
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0
    cost_usd: float | None = None


class ProviderReport(BaseModel):
    provider: ProviderId
    display_name: str
    source: SourceKind
    period_start: date | None = None
    period_end: date | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0
    cost_usd: float | None = None
    currency: str = "USD"
    models: list[ModelUsage] = Field(default_factory=list)
    daily: list[DailyPoint] = Field(default_factory=list)
    # Free-form extras: quota %, plan name, remaining credits, console links…
    meta: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=_utc_now)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @property
    def ok(self) -> bool:
        return self.source != SourceKind.UNAVAILABLE and not (
            self.errors and self.total_tokens == 0 and self.cost_usd is None
        )


class AggregateReport(BaseModel):
    period_start: date
    period_end: date
    providers: list[ProviderReport] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)

    @property
    def total_cost_usd(self) -> float | None:
        costs = [p.cost_usd for p in self.providers if p.cost_usd is not None]
        if not costs:
            return None
        return sum(costs)

    @property
    def total_tokens(self) -> int:
        return sum(p.total_tokens for p in self.providers)

    @property
    def total_requests(self) -> int:
        return sum(p.requests for p in self.providers)
