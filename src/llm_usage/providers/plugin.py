"""Optional plugin loader for custom provider collectors.

Plugins live under ``~/.config/llm-usage/plugins/*.py``. Each file may define
a class subclassing :class:`ProviderPlugin`. Plugins are loaded only from
that user-owned directory (never from the current working directory).
"""

from __future__ import annotations

import importlib.util
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


def load_plugin(plugin_path: Path) -> ProviderPlugin | None:
    """Load a provider plugin from a Python file under the plugins directory."""
    if not plugin_path.is_file():
        return None
    # Only allow files inside the plugins dir (never CWD or arbitrary paths).
    try:
        plugins_root = _plugins_dir().resolve()
        resolved = plugin_path.resolve()
        resolved.relative_to(plugins_root)
    except (OSError, ValueError):
        return None

    try:
        spec = importlib.util.spec_from_file_location(
            f"llm_usage_plugin_{plugin_path.stem}", resolved
        )
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, ProviderPlugin)
                and attr is not ProviderPlugin
            ):
                return attr()
        return None
    except Exception:  # noqa: BLE001
        return None


def load_plugins_from_dir(plugins_dir: Path | None = None) -> list[ProviderPlugin]:
    plugins_dir = plugins_dir or _plugins_dir()
    if not plugins_dir.is_dir():
        return []

    plugins: list[ProviderPlugin] = []
    for plugin_file in sorted(plugins_dir.glob("*.py")):
        if plugin_file.name.startswith("_"):
            continue
        plugin = load_plugin(plugin_file)
        if plugin:
            plugins.append(plugin)
    return plugins


def get_custom_providers(settings: Settings) -> list[ProviderReport]:
    """Collect usage reports from all installed custom plugins."""
    reports: list[ProviderReport] = []
    for plugin in load_plugins_from_dir():
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
