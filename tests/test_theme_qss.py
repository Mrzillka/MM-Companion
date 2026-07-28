"""What the generated application stylesheet may and may not contain.

The two hard rules in :mod:`mm_companion.ui.theme.qss` are guarded here rather
than only stated in a comment, because breaking either fails silently: an
unscoped selector quietly repaints every label nested in a card, and a
stylesheet ``font-size`` quietly stops the powers cards animating.
"""

from __future__ import annotations

import re

import pytest

from mm_companion.ui.theme import loader, qss

BUNDLED = ["classic", "slate-dark", "parchment-light"]


@pytest.fixture
def presets():
    return loader.available_themes()


def test_a_system_preset_emits_no_widget_chrome(presets) -> None:
    """Classic must leave every widget in the native platform style."""
    sheet = qss.build(presets["classic"])

    assert "focus ring" in sheet  # the one additive thing it does emit
    for absent in ("#blockFrame", "#blockTitleBar", "QMainWindow", "QHeaderView", "background:"):
        assert absent not in sheet, f"system preset should not style {absent}"


def test_a_system_preset_with_the_ring_off_emits_nothing_at_all(presets) -> None:
    from dataclasses import replace

    from mm_companion.ui.theme.tokens import Chrome

    bare = replace(presets["classic"], chrome=Chrome(mode="system", focus_ring=False))
    assert qss.build(bare) == ""


@pytest.mark.parametrize("theme_id", ["slate-dark", "parchment-light"])
def test_a_styled_preset_dresses_the_window(presets, theme_id: str) -> None:
    sheet = qss.build(presets[theme_id])

    for expected in ("#blockFrame", "#blockTitleBar", "#blockCanvas", "QMainWindow"):
        assert expected in sheet


@pytest.mark.parametrize("theme_id", BUNDLED)
def test_no_preset_selects_a_bare_container_class(presets, theme_id: str) -> None:
    """An unscoped QFrame/QLabel rule is inherited by every child of every card.

    That is how a border meant for one panel ends up drawn around each separator
    and label inside it, so those selectors are banned outright.
    """
    sheet = qss.build(presets[theme_id])

    for selector in ("QFrame", "QLabel", "QGroupBox", "QScrollArea"):
        # Match the selector only at the head of a rule, so "QLabel" inside a
        # comment or a longer name (QLabelish) doesn't trip it.
        pattern = rf"(^|[,\s]){selector}\s*(\{{|,|:)"
        assert not re.search(
            pattern, sheet, re.MULTILINE
        ), f"{theme_id} styles a bare {selector}, which every nested widget inherits"


@pytest.mark.parametrize("theme_id", BUNDLED)
def test_no_preset_sets_a_font_size(presets, theme_id: str) -> None:
    """Sizes belong on the QFont; a QSS font-size outranks it.

    The powers section animates a switched-off card by interpolating point sizes
    through ``QFont.setPointSizeF``. A stylesheet size anywhere above such a card
    pins it, and the transition silently stops working.
    """
    assert "font-size" not in qss.build(presets[theme_id])


@pytest.mark.parametrize("theme_id", BUNDLED)
def test_every_token_a_preset_needs_to_build_is_present(presets, theme_id: str) -> None:
    """Building must not raise — a styled preset missing a surface fails here."""
    assert isinstance(qss.build(presets[theme_id]), str)
