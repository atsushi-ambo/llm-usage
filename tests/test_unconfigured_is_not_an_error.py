"""An unconfigured provider must not look like a broken one.

`doctor` and `check` exit non-zero on `report.errors`, so a provider that
merely lacks an API key has to report that as a *note*. When several
providers pushed "KEY not set" into `errors` instead, `llm-usage doctor`
exited 1 on a perfectly healthy install — which makes it useless as a
health check and would fail any CI job that runs it.
"""

from __future__ import annotations

from datetime import date

import pytest

from llm_usage.config import Settings
from llm_usage.models import SourceKind
from llm_usage.providers.cohere import collect as collect_cohere
from llm_usage.providers.huggingface import collect as collect_huggingface
from llm_usage.providers.mistral import collect as collect_mistral
from llm_usage.providers.replicate import collect as collect_replicate
from llm_usage.providers.xai import collect_xai

START = date(2026, 7, 1)
END = date(2026, 7, 7)

# Providers whose only "unconfigured" signal is a missing API key.
KEY_ONLY_COLLECTORS = [
    pytest.param(collect_cohere, "COHERE_API_KEY", id="cohere"),
    pytest.param(collect_huggingface, "HUGGINGFACE_API_KEY", id="huggingface"),
    pytest.param(collect_mistral, "MISTRAL_API_KEY", id="mistral"),
    pytest.param(collect_replicate, "REPLICATE_API_KEY", id="replicate"),
]


@pytest.mark.parametrize(("collector", "env_var"), KEY_ONLY_COLLECTORS)
def test_missing_key_is_a_note_not_an_error(collector, env_var):
    report = collector(Settings(_env_file=None))
    assert report.source == SourceKind.UNAVAILABLE
    assert report.errors == [], f"{env_var} missing should not be an error"
    assert any(env_var in n for n in report.notes)


def test_grok_without_login_is_a_note_not_an_error(tmp_path):
    """No ~/.grok/auth.json means 'not set up yet', not 'billing fetch failed'."""
    settings = Settings(_env_file=None, GROK_HOME=str(tmp_path / "empty-grok"))
    report = collect_xai(settings, START, END)
    assert report.errors == []
    assert any("grok login" in n for n in report.notes)
