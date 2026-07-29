"""The theme token layer: presets parse, resolve, and reproduce the old look.

Headless — nothing here needs Qt.
"""

from __future__ import annotations

import json

import pytest

from mm_companion.core import storage
from mm_companion.ui import theme
from mm_companion.ui.theme import loader, tokens


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Point the workspace at an empty temp dir and reset the theme cache around it.

    The active preset is cached (token lookups happen constantly, and each one
    would otherwise re-read settings.json), so a test that moves the workspace has
    to invalidate it on the way in *and* on the way out.
    """
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield tmp_path
    theme.reset()


def write_preset(root, name: str, body: dict) -> None:
    directory = root / "themes"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


# -- discovery and parsing ------------------------------------------------------


def test_the_bundled_presets_all_parse() -> None:
    themes = loader.available_themes()

    assert {"classic", "slate-dark", "parchment-light"} <= set(themes)
    for preset in themes.values():
        assert preset.name
        assert preset.chrome.mode in tokens.CHROME_MODES


def test_classic_is_the_default_and_leaves_the_widgets_native() -> None:
    assert loader.resolve_id(None) == "classic"
    assert theme.active_theme().id == "classic"
    assert not theme.active_theme().styled


def test_a_legacy_system_setting_still_resolves() -> None:
    """Workspaces written before presets existed carry ``"theme": "system"``."""
    assert loader.resolve_id("system") == "classic"
    assert loader.resolve_id("") == "classic"


def test_an_unknown_theme_id_falls_back_rather_than_raising() -> None:
    assert loader.resolve_id("no-such-preset") == "classic"


def test_extends_inherits_every_token_the_child_does_not_restate(isolated_workspace) -> None:
    write_preset(
        isolated_workspace,
        "child",
        {"id": "child", "name": "Child", "extends": "classic", "colors": {"accent": "#123456"}},
    )
    theme.reset()
    child = loader.available_themes()["child"]

    assert child.colors["accent"] == "#123456"  # overridden
    assert child.colors["tint.worse"] == "#d15b5b"  # inherited
    assert child.metrics["radius.card"] == 4  # inherited from another group


def test_a_workspace_preset_replaces_a_bundled_one_with_the_same_id(isolated_workspace) -> None:
    write_preset(
        isolated_workspace,
        "classic",
        {"id": "classic", "name": "Mine", "colors": {"accent": "#000000"}},
    )
    theme.reset()

    assert loader.available_themes()["classic"].name == "Mine"


def test_a_malformed_workspace_preset_is_skipped_not_fatal(isolated_workspace) -> None:
    (isolated_workspace / "themes").mkdir()
    (isolated_workspace / "themes" / "broken.json").write_text("{ not json", encoding="utf-8")
    write_preset(isolated_workspace, "fine", {"id": "fine", "name": "Fine", "extends": "classic"})
    theme.reset()

    themes = loader.available_themes()
    assert "broken" not in themes
    assert "fine" in themes  # the good one alongside it still loaded


def test_a_bad_chrome_mode_is_rejected(isolated_workspace) -> None:
    write_preset(isolated_workspace, "odd", {"id": "odd", "chrome": {"mode": "sideways"}})
    theme.reset()

    assert "odd" not in loader.available_themes()


def test_an_inline_comment_key_is_not_a_token() -> None:
    """A preset documents itself inline; those notes must not arrive as tokens.

    ``classic.json`` explains its retuned amber in a ``"_tint.warning"`` key
    sitting inside ``colors``. Anything iterating the map — the Settings editor
    above all — would otherwise render a colour row whose value is prose.
    """
    preset = loader.available_themes()["classic"]

    assert "_tint.warning" not in preset.colors
    for group in (preset.colors, preset.metrics, preset.typography, preset.blocks):
        assert not [key for key in group if key.startswith("_")]


def test_a_self_referential_extends_does_not_recurse(isolated_workspace) -> None:
    write_preset(
        isolated_workspace,
        "loop",
        {"id": "loop", "name": "Loop", "extends": "loop", "colors": {"accent": "#abcdef"}},
    )
    theme.reset()

    assert loader.available_themes()["loop"].colors["accent"] == "#abcdef"


# -- token lookup ---------------------------------------------------------------


def test_an_unknown_token_names_itself_and_suggests_neighbours() -> None:
    with pytest.raises(theme.UnknownToken) as excinfo:
        theme.color("tint.wrose")

    message = str(excinfo.value)
    assert "tint.wrose" in message
    assert "tint.worse" in message  # the did-you-mean found it


def test_a_wash_refuses_a_palette_expression() -> None:
    """``palette(mid)`` is resolved by Qt at paint time and has no channels here."""
    assert not theme.is_literal_color(theme.color("border.card"))
    with pytest.raises(ValueError, match="translucent wash"):
        theme.wash("border.card", 0.1)

    assert theme.wash("accent", 0.1) == "rgba(68, 136, 207, 0.1)"


def test_box_returns_four_numbers_and_rejects_a_scalar() -> None:
    assert theme.box("card.margins") == (8, 6, 8, 6)
    with pytest.raises(ValueError, match="four-number box"):
        theme.box("radius.card")


# -- the "classic changes nothing" guarantee ------------------------------------

#: Every literal the UI carried before the token layer existed, so a retune of
#: Classic that silently changes the historical look fails here rather than in a
#: screenshot nobody takes. Sourced from the pre-refactor constants: ui/theme.py's
#: four tints, powers.py's group border and card metrics, the constructor's chip
#: and role-badge fills, and the column minimums in skills.py / advantages.py.
CLASSIC_COLORS = {
    "tint.better": "#30964e",
    "tint.worse": "#d15b5b",
    "accent": "#4488cf",
    "accent.dice": "#6784bf",
    # Not the historical #d1a01e: that literal never went through the contrast
    # pass (it lived outside theme.py) and failed it at 2.10:1 on a light
    # window. This is the retuned value — see classic.json.
    "tint.warning": "#b47030",
    "border.group": "#8894b0",
    "border.card": "palette(mid)",
    "text.muted": "palette(placeholder-text)",
    "badge.extra": "#2e5e33",
    "badge.flaw": "#5e2e2e",
    "badge.flat": "#555555",
    "badge.role.base": "#3a5f8a",
    "badge.role.alternate": "#7a5c1e",
    "badge.role.linked": "#3a6f4a",
    "drop.indicator": "palette(highlight)",
    "splash.background": "#2b2b3a",
}

CLASSIC_METRICS = {
    "radius.card": 4,
    "radius.group": 6,
    "radius.canvas": 8,
    "radius.chip": 6,
    "border.width": 1,
    "border.width.emphasis": 2,
    "border.width.accent-edge": 3,
    "opacity.inactive": 0.5,
    "canvas.hint.height": 120,
    "column.stat.spin": 80,
    "column.skill.name": 100,
    "column.skill.spin": 56,
    "column.skill.mod": 36,
    "column.advantage.desc": 180,
    "column.advantage.combo": 180,
}

CLASSIC_TYPOGRAPHY = {
    "size.card-name": 11.0,
    "size.terms": 8.0,
    "size.cost-total": 12.0,
    "size.dice-face": 40.0,
    "scale.inactive": 0.9,
}


@pytest.mark.parametrize("name,expected", sorted(CLASSIC_COLORS.items()))
def test_classic_reproduces_the_historical_colour(name: str, expected: str) -> None:
    assert theme.color(name) == expected


@pytest.mark.parametrize("name,expected", sorted(CLASSIC_METRICS.items()))
def test_classic_reproduces_the_historical_metric(name: str, expected: float) -> None:
    assert theme.metric(name) == expected


@pytest.mark.parametrize("name,expected", sorted(CLASSIC_TYPOGRAPHY.items()))
def test_classic_reproduces_the_historical_point_size(name: str, expected: float) -> None:
    assert theme.font_size(name) == expected


def test_classic_keeps_the_platform_font() -> None:
    assert theme.font_family() == ""


# -- contrast -------------------------------------------------------------------

#: The tints that style text and therefore have to stay legible.
SEMANTIC_TINTS = ("tint.better", "tint.worse", "tint.warning", "accent", "accent.dice")


@pytest.mark.parametrize("theme_id", ["classic", "slate-dark", "parchment-light"])
def test_every_preset_keeps_its_tints_legible(theme_id: str) -> None:
    preset = loader.available_themes()[theme_id]
    backgrounds = tokens.measurement_backdrops(preset)
    assert backgrounds, f"{theme_id} declares no surfaces to measure against"

    for token in SEMANTIC_TINTS:
        colour = preset.colors[token]
        for background in backgrounds:
            ratio = tokens.contrast_ratio(colour, background)
            assert ratio >= tokens.MIN_CONTRAST, (
                f"{theme_id}: {token} ({colour}) on {background} is {ratio:.2f}:1, "
                f"below the {tokens.MIN_CONTRAST}:1 floor"
            )


def test_a_styled_preset_does_better_than_the_both_themes_compromise() -> None:
    """Knowing its own surface, a styled preset can clear AA for normal text too.

    Classic cannot: one fixed hue has to sit between a light and a dark window and
    lands around 3.2:1. This is the payoff for a preset that commits to a look.
    """
    for theme_id in ("slate-dark", "parchment-light"):
        preset = loader.available_themes()[theme_id]
        window = preset.colors["surface.window"]
        for token in ("tint.better", "tint.worse"):
            assert tokens.contrast_ratio(preset.colors[token], window) >= 4.5


# -- switching ------------------------------------------------------------------


def test_switching_persists_the_choice_and_reaches_the_next_lookup() -> None:
    assert theme.color("accent") == "#4488cf"

    theme.set_active_theme("slate-dark")

    assert storage.load_settings()["theme"] == "slate-dark"
    assert theme.active_theme().id == "slate-dark"
    assert theme.color("accent") == "#5b9ee0"
