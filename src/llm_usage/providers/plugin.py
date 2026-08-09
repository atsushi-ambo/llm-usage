"""Optional plugin loader for custom provider collectors.

Plugins live under ``~/.config/llm-usage/plugins/*.py``. Each file may define
a class subclassing :class:`ProviderPlugin`. Plugins are loaded only from
that user-owned directory (never from the current working directory).

**Trust boundary:** a plugin is arbitrary Python executed in-process, and
this process reads live OAuth tokens from Claude Code / Codex / Grok
credential stores. A plugin therefore runs with full access to those
tokens and to every configured API key — it is exactly as trusted as
llm-usage itself. To keep "user-owned directory" a real guarantee rather
than an assumption, the loader refuses anything that another local
account could have tampered with: the plugins directory and each plugin
file must be owned by you (or root) and must not be group/world-writable,
and a symlink escaping the plugins directory is rejected.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

from llm_usage.config import Settings
from llm_usage.models import ProviderId, ProviderReport, SourceKind


class ProviderPlugin:
    """Base class for custom provider plugins."""

    name: str = "custom"
    version: str = "1.0.0"

    def collect(self, settings: Settings) -> ProviderReport:
        raise NotImplementedError("Plugin must implement collect()")

    def validate_config(self, settings: Settings) -> list[str]:
        return []


def _plugins_dir() -> Path:
    return Path.home() / ".config" / "llm-usage" / "plugins"


def unsafe_permission_reason(path: Path) -> str | None:
    """Why `path` is not safe to execute code from, or None if it's fine.

    Follows symlinks deliberately: callers verify the resolved path stays
    inside the plugins directory first, so what matters here is the mode of
    the real file being executed, not of the link pointing at it.
    """
    if sys.platform == "win32":
        # POSIX mode bits carry no meaningful ACL information on Windows;
        # NTFS permissions would need a different check entirely.
        return None
    try:
        st = path.stat()
    except OSError as exc:
        return f"cannot stat ({exc.strerror or exc})"
    if st.st_uid not in (os.getuid(), 0):
        return "not owned by you"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "writable by group or others"
    return None


def _load_plugin_with_reason(plugin_path: Path) -> tuple[ProviderPlugin | None, str | None]:
    """Load one plugin, returning (plugin, skip_reason).

    Exactly one of the two is non-None on a definitive outcome; both are
    None when the file simply contains no ProviderPlugin subclass.
    """
    if not plugin_path.is_file():
        return None, "not a file"

    # Only allow files inside the plugins dir (never CWD or arbitrary paths).
    # resolve() collapses symlinks, so a link pointing outside fails here.
    try:
        plugins_root = _plugins_dir().resolve()
        resolved = plugin_path.resolve()
        resolved.relative_to(plugins_root)
    except (OSError, ValueError):
        return None, "outside the plugins directory"

    reason = unsafe_permission_reason(resolved)
    if reason:
        return None, reason

    module_name = f"llm_usage_plugin_{plugin_path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, resolved)
        if spec is None or spec.loader is None:
            return None, "could not be imported"

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            # Don't leave a half-initialized module behind for the next import.
            sys.modules.pop(spec.name, None)
            raise

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, ProviderPlugin)
                and attr is not ProviderPlugin
            ):
                return attr(), None
        return None, None  # imported fine, just defines no plugin
    except Exception as exc:  # noqa: BLE001
        return None, f"failed to import ({type(exc).__name__}: {exc})"


def load_plugin(plugin_path: Path) -> ProviderPlugin | None:
    """Load a provider plugin from a Python file under the plugins directory."""
    return _load_plugin_with_reason(plugin_path)[0]


def load_plugins_with_diagnostics(
    plugins_dir: Path | None = None,
) -> tuple[list[ProviderPlugin], list[tuple[str, str]]]:
    """Load every plugin, also returning (filename, reason) for skipped ones.

    Skips are surfaced rather than swallowed: a plugin silently vanishing
    because its file became group-writable is exactly the case a user needs
    told about.
    """
    plugins_dir = plugins_dir or _plugins_dir()
    if not plugins_dir.is_dir():
        return [], []

    dir_reason = unsafe_permission_reason(plugins_dir)
    if dir_reason:
        return [], [(plugins_dir.name, f"plugins directory {dir_reason}")]

    plugins: list[ProviderPlugin] = []
    skipped: list[tuple[str, str]] = []
    for plugin_file in sorted(plugins_dir.glob("*.py")):
        if plugin_file.name.startswith("_"):
            continue
        plugin, reason = _load_plugin_with_reason(plugin_file)
        if plugin is not None:
            plugins.append(plugin)
        elif reason:
            skipped.append((plugin_file.name, reason))
    return plugins, skipped


def load_plugins_from_dir(plugins_dir: Path | None = None) -> list[ProviderPlugin]:
    return load_plugins_with_diagnostics(plugins_dir)[0]


def get_custom_providers(settings: Settings) -> list[ProviderReport]:
    """Collect usage reports from all installed custom plugins."""
    reports: list[ProviderReport] = []
    plugins, skipped = load_plugins_with_diagnostics()

    for filename, reason in skipped:
        reports.append(
            ProviderReport(
                provider=ProviderId.LOCAL,
                display_name=f"Plugin: {filename}",
                source=SourceKind.UNAVAILABLE,
                errors=[f"Skipped — {reason}"],
            )
        )

    for plugin in plugins:
        display = getattr(plugin, "name", None) or "custom"
        try:
            errors = plugin.validate_config(settings)
            if errors:
                reports.append(
                    ProviderReport(
                        provider=ProviderId.LOCAL,
                        display_name=str(display),
                        source=SourceKind.UNAVAILABLE,
                        errors=list(errors),
                    )
                )
                continue
            report = plugin.collect(settings)
            if not isinstance(report, ProviderReport):
                raise TypeError("plugin.collect() must return ProviderReport")
            reports.append(report)
        except Exception as e:  # noqa: BLE001
            reports.append(
                ProviderReport(
                    provider=ProviderId.LOCAL,
                    display_name=str(display),
                    source=SourceKind.UNAVAILABLE,
                    errors=[f"Plugin error: {e}"],
                )
            )
    return reports
