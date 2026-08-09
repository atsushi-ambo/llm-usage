"""Tests for the custom-provider plugin loader.

A plugin is arbitrary Python executed in the same process that holds live
OAuth tokens for Claude Code / Codex / Grok, so "only load from the
user-owned plugins directory" has to be enforced, not assumed. These tests
pin the containment rules: no path traversal, no symlink escape, and
nothing another local account could have written to.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import llm_usage.providers.plugin as plugin_module
from llm_usage.config import Settings
from llm_usage.models import ProviderId, SourceKind
from llm_usage.providers.plugin import (
    ProviderPlugin,
    _load_plugin_with_reason,
    get_custom_providers,
    load_plugin,
    load_plugins_from_dir,
    load_plugins_with_diagnostics,
    unsafe_permission_reason,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")

GOOD_PLUGIN = '''
from llm_usage.providers.plugin import ProviderPlugin
from llm_usage.models import ProviderId, ProviderReport, SourceKind


class MyPlugin(ProviderPlugin):
    name = "my-plugin"

    def collect(self, settings):
        return ProviderReport(
            provider=ProviderId.LOCAL,
            display_name="my-plugin",
            source=SourceKind.LOCAL_LOGS,
            requests=42,
        )
'''


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch) -> Path:
    """A plugins dir that the loader treats as the real one."""
    d = tmp_path / "plugins"
    d.mkdir(mode=0o700)
    monkeypatch.setattr(plugin_module, "_plugins_dir", lambda: d)
    return d


def _write(d: Path, name: str, body: str, mode: int = 0o600) -> Path:
    p = d / name
    p.write_text(body)
    p.chmod(mode)
    return p


# ── happy path ────────────────────────────────────────────────────────


def test_loads_a_well_formed_plugin(plugins_dir):
    _write(plugins_dir, "good.py", GOOD_PLUGIN)
    plugins = load_plugins_from_dir()
    assert len(plugins) == 1
    assert plugins[0].name == "my-plugin"


def test_missing_plugins_dir_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module, "_plugins_dir", lambda: tmp_path / "nope")
    assert load_plugins_with_diagnostics() == ([], [])


def test_underscore_prefixed_files_are_ignored(plugins_dir):
    _write(plugins_dir, "_helper.py", GOOD_PLUGIN)
    plugins, skipped = load_plugins_with_diagnostics()
    assert plugins == [] and skipped == []


def test_file_without_a_plugin_class_is_not_reported_as_an_error(plugins_dir):
    _write(plugins_dir, "plain.py", "VALUE = 1\n")
    plugins, skipped = load_plugins_with_diagnostics()
    assert plugins == [] and skipped == []


def test_plugins_load_in_sorted_order(plugins_dir):
    _write(plugins_dir, "b.py", GOOD_PLUGIN.replace('"my-plugin"', '"b"'))
    _write(plugins_dir, "a.py", GOOD_PLUGIN.replace('"my-plugin"', '"a"'))
    assert [p.name for p in load_plugins_from_dir()] == ["a", "b"]


# ── containment ───────────────────────────────────────────────────────


def test_rejects_file_outside_the_plugins_dir(plugins_dir, tmp_path):
    outside = tmp_path / "evil.py"
    outside.write_text(GOOD_PLUGIN)
    outside.chmod(0o600)
    plugin, reason = _load_plugin_with_reason(outside)
    assert plugin is None
    assert reason == "outside the plugins directory"


def test_rejects_path_traversal_out_of_the_plugins_dir(plugins_dir, tmp_path):
    outside = tmp_path / "evil.py"
    outside.write_text(GOOD_PLUGIN)
    outside.chmod(0o600)
    assert load_plugin(plugins_dir / ".." / "evil.py") is None


@posix_only
def test_rejects_symlink_escaping_the_plugins_dir(plugins_dir, tmp_path):
    """A symlink inside the dir must not smuggle in code from outside."""
    target = tmp_path / "evil.py"
    target.write_text(GOOD_PLUGIN)
    target.chmod(0o600)
    (plugins_dir / "link.py").symlink_to(target)

    plugins, skipped = load_plugins_with_diagnostics()
    assert plugins == []
    assert skipped == [("link.py", "outside the plugins directory")]


# ── permissions ───────────────────────────────────────────────────────


@posix_only
@pytest.mark.parametrize("mode", [0o666, 0o620, 0o602, 0o777])
def test_rejects_plugin_file_writable_by_group_or_others(plugins_dir, mode):
    _write(plugins_dir, "loose.py", GOOD_PLUGIN, mode=mode)
    plugins, skipped = load_plugins_with_diagnostics()
    assert plugins == []
    assert skipped == [("loose.py", "writable by group or others")]


@posix_only
@pytest.mark.parametrize("mode", [0o777, 0o770, 0o707])
def test_rejects_world_or_group_writable_plugins_dir(plugins_dir, mode):
    _write(plugins_dir, "good.py", GOOD_PLUGIN)
    plugins_dir.chmod(mode)
    try:
        plugins, skipped = load_plugins_with_diagnostics()
        assert plugins == []
        assert len(skipped) == 1
        assert "writable by group or others" in skipped[0][1]
    finally:
        plugins_dir.chmod(0o700)


@posix_only
def test_accepts_owner_only_and_owner_readable_modes(plugins_dir):
    _write(plugins_dir, "a.py", GOOD_PLUGIN, mode=0o600)
    _write(plugins_dir, "b.py", GOOD_PLUGIN.replace('"my-plugin"', '"b"'), mode=0o644)
    assert len(load_plugins_from_dir()) == 2


class _StubPath:
    """Minimal stand-in so ownership checks don't depend on the test user.

    Real chown needs privileges the test suite may not have, and running as
    root would make every file look legitimately owned.
    """

    def __init__(self, uid: int, mode: int = 0o100600) -> None:
        self._uid, self._mode = uid, mode

    def stat(self):
        class _S:
            st_uid = self._uid
            st_mode = self._mode

        return _S()


@posix_only
def test_unsafe_permission_reason_flags_foreign_owner(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    assert unsafe_permission_reason(_StubPath(uid=1234)) == "not owned by you"


@posix_only
def test_root_owned_files_are_trusted(monkeypatch):
    """Root-installed plugins are legitimate; only *other* users are suspect."""
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    assert unsafe_permission_reason(_StubPath(uid=0)) is None


@posix_only
def test_owner_check_precedes_mode_check(monkeypatch):
    """A foreign-owned file is rejected even with tight permissions."""
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    assert unsafe_permission_reason(_StubPath(uid=1234, mode=0o100600)) == "not owned by you"


def test_unsafe_permission_reason_reports_missing_path(tmp_path):
    reason = unsafe_permission_reason(tmp_path / "missing.py")
    assert reason is not None and "cannot stat" in reason


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific skip path")
def test_permission_check_is_skipped_on_windows(tmp_path):
    assert unsafe_permission_reason(tmp_path) is None


# ── failure handling ──────────────────────────────────────────────────


def test_plugin_that_raises_on_import_is_reported_not_swallowed(plugins_dir):
    _write(plugins_dir, "boom.py", "raise RuntimeError('bad plugin')\n")
    plugins, skipped = load_plugins_with_diagnostics()
    assert plugins == []
    assert len(skipped) == 1
    name, reason = skipped[0]
    assert name == "boom.py"
    assert "RuntimeError" in reason and "bad plugin" in reason


def test_failed_import_does_not_leave_a_module_behind(plugins_dir):
    _write(plugins_dir, "boom.py", "raise RuntimeError('bad')\n")
    load_plugins_with_diagnostics()
    assert "llm_usage_plugin_boom" not in sys.modules


def test_syntax_error_is_reported(plugins_dir):
    _write(plugins_dir, "bad.py", "def (\n")
    _, skipped = load_plugins_with_diagnostics()
    assert len(skipped) == 1
    assert "SyntaxError" in skipped[0][1]


# ── get_custom_providers surfaces problems as reports ─────────────────


def test_get_custom_providers_returns_plugin_report(plugins_dir):
    _write(plugins_dir, "good.py", GOOD_PLUGIN)
    reports = get_custom_providers(Settings(_env_file=None))
    assert len(reports) == 1
    assert reports[0].requests == 42
    assert reports[0].source == SourceKind.LOCAL_LOGS


@posix_only
def test_get_custom_providers_surfaces_skipped_plugin_as_error_report(plugins_dir):
    _write(plugins_dir, "loose.py", GOOD_PLUGIN, mode=0o666)
    reports = get_custom_providers(Settings(_env_file=None))
    assert len(reports) == 1
    r = reports[0]
    assert r.source == SourceKind.UNAVAILABLE
    assert "loose.py" in r.display_name
    assert any("writable by group or others" in e for e in r.errors)


def test_get_custom_providers_reports_collect_failure(plugins_dir):
    _write(
        plugins_dir,
        "raises.py",
        "from llm_usage.providers.plugin import ProviderPlugin\n"
        "class P(ProviderPlugin):\n"
        "    name = 'raiser'\n"
        "    def collect(self, settings):\n"
        "        raise ValueError('collect blew up')\n",
    )
    reports = get_custom_providers(Settings(_env_file=None))
    assert len(reports) == 1
    assert reports[0].source == SourceKind.UNAVAILABLE
    assert any("collect blew up" in e for e in reports[0].errors)


def test_get_custom_providers_rejects_wrong_return_type(plugins_dir):
    _write(
        plugins_dir,
        "wrong.py",
        "from llm_usage.providers.plugin import ProviderPlugin\n"
        "class P(ProviderPlugin):\n"
        "    name = 'wrong'\n"
        "    def collect(self, settings):\n"
        "        return {'not': 'a report'}\n",
    )
    reports = get_custom_providers(Settings(_env_file=None))
    assert reports[0].source == SourceKind.UNAVAILABLE
    assert any("ProviderReport" in e for e in reports[0].errors)


def test_get_custom_providers_reports_validation_errors(plugins_dir):
    _write(
        plugins_dir,
        "invalid.py",
        "from llm_usage.providers.plugin import ProviderPlugin\n"
        "class P(ProviderPlugin):\n"
        "    name = 'needs-config'\n"
        "    def validate_config(self, settings):\n"
        "        return ['MY_API_KEY is not set']\n"
        "    def collect(self, settings):\n"
        "        raise AssertionError('must not be called')\n",
    )
    reports = get_custom_providers(Settings(_env_file=None))
    assert reports[0].provider == ProviderId.LOCAL
    assert reports[0].errors == ["MY_API_KEY is not set"]


def test_base_plugin_collect_is_abstract():
    with pytest.raises(NotImplementedError):
        ProviderPlugin().collect(Settings(_env_file=None))
