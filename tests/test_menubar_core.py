"""Tests for the AppKit-free half of the menu bar.

These cover the parts that were previously unreachable from tests because
they were tangled with AppKit: palette resolution for light vs dark menus,
provider lookup, and preference persistence.
"""

from __future__ import annotations

from datetime import date

import pytest

import llm_usage.menubar_core as core
from llm_usage.menubar_core import (
    DEFAULT_FOCUS,
    FOCUS_ORDER,
    PROVIDER_STYLE,
    _RGB_EMPTY_DARK,
    _RGB_EMPTY_LIGHT,
    bar_segments,
    brighten,
    build_palette,
    find_provider,
    load_prefs,
    save_prefs,
)
from llm_usage.models import AggregateReport, ProviderId, ProviderReport, SourceKind


def _report(*providers: ProviderReport) -> AggregateReport:
    return AggregateReport(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 18),
        providers=list(providers),
    )


def _p(pid: ProviderId, name: str | None = None) -> ProviderReport:
    return ProviderReport(
        provider=pid,
        display_name=name or pid.value,
        source=SourceKind.SUBSCRIPTION,
    )


# ── palette ───────────────────────────────────────────────────────────


def test_light_palette_uses_baseline_brand_colors():
    pal = build_palette(dark=False)
    assert pal["dark"] is False
    assert pal["empty"] == _RGB_EMPTY_LIGHT
    for key, style in PROVIDER_STYLE.items():
        assert pal["brands"][key] == style["rgb"]


def test_dark_palette_brightens_every_brand_and_heat_color():
    light, dark = build_palette(dark=False), build_palette(dark=True)
    assert dark["dark"] is True
    assert dark["empty"] == _RGB_EMPTY_DARK
    for key in PROVIDER_STYLE:
        assert dark["brands"][key] == brighten(light["brands"][key])
    for heat in ("ok", "warn", "hot", "crit"):
        assert dark[heat] == brighten(light[heat])


def test_palette_covers_every_provider_in_focus_order():
    """A provider listed in the switcher must have a color to draw with."""
    brands = build_palette(dark=False)["brands"]
    missing = [pid for pid in FOCUS_ORDER if pid not in brands]
    assert missing == []


def test_default_focus_is_a_known_provider():
    assert DEFAULT_FOCUS in PROVIDER_STYLE
    assert DEFAULT_FOCUS in FOCUS_ORDER


def test_brighten_saturates_at_255_and_never_overflows():
    assert brighten((255, 255, 255)) == (255, 255, 255)
    assert all(0 <= c <= 255 for c in brighten((250, 200, 10), factor=3.0))


def test_brighten_is_monotonic():
    base = (100, 120, 140)
    assert all(b >= a for a, b in zip(base, brighten(base)))


# ── provider lookup ───────────────────────────────────────────────────


def test_find_provider_matches_by_id():
    rep = _report(_p(ProviderId.CLAUDE), _p(ProviderId.GROK))
    assert find_provider(rep, "grok").provider == ProviderId.GROK
    assert find_provider(rep, "cursor") is None


def test_openai_falls_back_to_the_merged_codex_card():
    """collect_all merges OpenAI + Codex into one card filed under codex."""
    rep = _report(_p(ProviderId.CODEX, "OpenAI / Codex"))
    found = find_provider(rep, "openai")
    assert found is not None and found.display_name == "OpenAI / Codex"


def test_find_provider_prefers_exact_id_over_merge_fallback():
    rep = _report(_p(ProviderId.CODEX, "codex-card"), _p(ProviderId.OPENAI, "openai-card"))
    assert find_provider(rep, "openai").display_name == "openai-card"


# ── bars ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pct", "expected_filled"),
    [
        (0, 0),
        # Python's round() is banker's rounding, so 5% of a 10-cell bar
        # (exactly 0.5 cells) floors to 0 rather than showing a sliver.
        (5, 0),
        (6, 1),
        (50, 5),
        (94, 9),
        (99, 10),
        (100, 10),
    ],
)
def test_bar_segments_fill_tracks_percentage(pct, expected_filled):
    segs = bar_segments(pct, width=10, brand=(1, 2, 3))
    assert sum(1 for ch, _ in segs if ch == "█") == expected_filled
    assert len(segs) == 10


def test_bar_segments_clamps_out_of_range_percentages():
    assert all(ch == "░" for ch, _ in bar_segments(-20, 8, (1, 2, 3)))
    assert all(ch == "█" for ch, _ in bar_segments(500, 8, (1, 2, 3)))


def test_bar_segments_empty_color_follows_the_palette():
    dark_empty = build_palette(dark=True)["empty"]
    segs = bar_segments(0, width=4, brand=(1, 2, 3), empty=dark_empty)
    assert {rgb for _, rgb in segs} == {dark_empty}


# ── preferences ───────────────────────────────────────────────────────


def test_prefs_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PREFS_PATH", tmp_path / "sub" / "menubar.json")
    save_prefs({"focus": "claude"})
    assert load_prefs()["focus"] == "claude"


def test_load_prefs_defaults_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PREFS_PATH", tmp_path / "missing.json")
    assert load_prefs() == {"focus": DEFAULT_FOCUS}


@pytest.mark.parametrize("junk", ["{not json", "[]", '"a string"'])
def test_load_prefs_survives_corrupt_file(tmp_path, monkeypatch, junk):
    path = tmp_path / "menubar.json"
    path.write_text(junk)
    monkeypatch.setattr(core, "PREFS_PATH", path)
    assert load_prefs() == {"focus": DEFAULT_FOCUS}


def test_save_prefs_is_written_with_owner_only_permissions(tmp_path, monkeypatch):
    """Prefs sit beside cached quota data in ~/.config; keep 0600 like the rest."""
    path = tmp_path / "menubar.json"
    monkeypatch.setattr(core, "PREFS_PATH", path)
    save_prefs({"focus": "grok"})
    assert path.stat().st_mode & 0o077 == 0
