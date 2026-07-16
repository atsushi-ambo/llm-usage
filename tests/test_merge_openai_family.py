from llm_usage.models import ProviderId, ProviderReport, SourceKind
from llm_usage.providers import _merge_openai_family


def _report(provider: ProviderId, **kwargs) -> ProviderReport:
    defaults = dict(
        provider=provider,
        display_name=provider.value,
        source=SourceKind.UNAVAILABLE,
    )
    defaults.update(kwargs)
    return ProviderReport(**defaults)


def test_codex_only_preserves_openai_errors():
    codex = _report(ProviderId.CODEX, source=SourceKind.LOCAL_LOGS, requests=5)
    openai = _report(ProviderId.OPENAI, errors=["HTTP 401 for https://api.openai.com/..."])

    merged = _merge_openai_family(codex, openai)
    assert merged.requests == 5
    assert any("401" in e for e in merged.errors)


def test_openai_only_preserves_codex_errors():
    codex = _report(ProviderId.CODEX, errors=["Codex quota API: HTTP 500 for ..."])
    openai = _report(ProviderId.OPENAI, source=SourceKind.API, cost_usd=1.23)

    merged = _merge_openai_family(codex, openai)
    assert merged.cost_usd == 1.23
    assert any("500" in e for e in merged.errors)


def test_neither_has_data_preserves_openai_errors():
    codex = _report(ProviderId.CODEX)
    openai = _report(ProviderId.OPENAI, errors=["HTTP 403 for https://api.openai.com/..."])

    merged = _merge_openai_family(codex, openai)
    assert any("403" in e for e in merged.errors)


def test_both_have_data_merges_errors_from_both():
    codex = _report(ProviderId.CODEX, source=SourceKind.LOCAL_LOGS, requests=1, errors=["codex broke"])
    openai = _report(ProviderId.OPENAI, source=SourceKind.API, cost_usd=2.0, errors=["openai broke"])

    merged = _merge_openai_family(codex, openai)
    assert any("codex broke" in e for e in merged.errors)
    assert any("openai broke" in e for e in merged.errors)
