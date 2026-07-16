import pytest

from llm_usage import quota as quota_module


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Never let tests write into the real ~/.config/llm-usage/cache.

    Individual tests (e.g. test_quota_cache.py) may still monkeypatch
    quota.cache_dir themselves for a specific tmp_path — that's fine, it
    just overrides this default for the duration of that test.
    """
    cache_root = tmp_path / "_llm_usage_cache"

    def _fake_cache_dir():
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root

    monkeypatch.setattr(quota_module, "cache_dir", _fake_cache_dir)
    return cache_root
