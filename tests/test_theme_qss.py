"""What the generated application stylesheet may and may not contain.

The hard rules in :mod:`mm_companion.ui.theme.qss` are guarded here rather than
only stated in a comment, because breaking any of them fails silently: an
unscoped selector quietly repaints every label nested in a card, a stylesheet
``font-size`` quietly stops the powers cards animating, and a box stated on a
complex widget without its sub-controls quietly lays the edit field on top of the
arrows — which still paint, and stop responding to a click.
"""

from __future__ import annotations

import re

import pytest

from mm_companion.ui.theme import loader, qss

BUNDLED = ["classic", "slate-dark", "parchment-light", "crimson-gold"]

#: The complex widgets this app puts on screen. Each keeps a column on its right
#: for arrows the platform style draws, and each stops being told about it the
#: moment the sheet states a box — so each needs that column back as padding.
COMPLEX_INPUTS = ("QSpinBox", "QDoubleSpinBox", "QComboBox")


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
def test_a_styled_preset_dresses_the_blocks_and_the_native_chrome(presets, theme_id: str) -> None:
    """The stylesheet states geometry, plus the chrome the native style paints itself.

    Colour reaches ordinary widgets through the palette, not through here — see
    :mod:`mm_companion.ui.theme.palette` for why. What is left for the sheet is
    the object-named block chrome, and the menus and tabs, which the Windows
    native style draws from the *system* theme and which therefore ignore the
    application palette entirely.
    """
    sheet = qss.build(presets[theme_id])

    for expected in ("#blockFrame", "#blockTitleBar", "#blockCanvas", "QMenuBar", "QTabBar::tab"):
        assert expected in sheet


@pytest.mark.parametrize("theme_id", ["slate-dark", "parchment-light", "crimson-gold"])
def test_a_styled_preset_says_what_a_checked_tool_button_looks_like(presets, theme_id: str) -> None:
    """The regression: a checkable tool button that looked exactly like an unchecked one.

    Stating a tool button's box at all makes ``QStyleSheetStyle`` take the whole box
    over and stop painting the platform's sunken/checked panel — so the mini
    roller's always-on-top pin, and a floated block's, simply gave no sign of being
    on. The resting border has to stay drawn (in ``transparent``) for the same
    reason ``QuickRollStar``'s does: otherwise the glyph jumps as it lights up.
    """
    sheet = qss.build(presets[theme_id])

    assert "QToolButton:checked" in sheet
    assert "border: none" not in sheet.split("/* buttons */")[1]


@pytest.mark.parametrize("theme_id", ["slate-dark", "parchment-light"])
def test_a_styled_preset_carries_its_colour_in_a_palette(presets, theme_id: str) -> None:
    from mm_companion.ui.theme.palette import build_palette

    palette = build_palette(presets[theme_id])
    assert palette is not None
    expected = presets[theme_id].colors["surface.window"]
    assert palette.window().color().name() == expected


def test_a_system_preset_installs_no_palette(presets) -> None:
    """Classic keeps the OS palette, and with it the OS light/dark setting."""
    from mm_companion.ui.theme.palette import build_palette

    assert build_palette(presets["classic"]) is None


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
def test_a_stated_menu_colour_restates_the_disabled_one(presets, theme_id: str) -> None:
    """State a menu's text colour, restate what disabled looks like.

    The same all-or-nothing bargain as ``QPushButton:checked``: once a property is
    stated, ``QStyleSheetStyle`` stops painting that property's states, so a flat
    ``color`` on ``QMenuBar`` paints a disabled action exactly like a live one. The
    bar carries real disabled actions — Undo and Redo with an empty history — and
    one that looks live but does nothing reads as broken rather than as empty.
    Classic states no colour and so keeps the platform's own painting.
    """
    rules = qss.build(presets[theme_id]).splitlines()

    for selector in ("QMenuBar", "QMenu"):
        states = [r for r in rules if r.startswith(f"{selector} ") and "color:" in r]
        if not states:
            continue  # Classic: no colour stated, so the platform still paints it
        restates = [
            r for r in rules if r.startswith(f"{selector}::item:disabled") and "color:" in r
        ]
        assert restates, (
            f"{theme_id} states {selector}'s colour ({states[0]}) without restating "
            "its disabled one, so a disabled entry paints exactly like a live one"
        )


@pytest.mark.parametrize("theme_id", BUNDLED)
def test_every_token_a_preset_needs_to_build_is_present(presets, theme_id: str) -> None:
    """Building must not raise — a styled preset missing a surface fails here."""
    assert isinstance(qss.build(presets[theme_id]), str)


@pytest.mark.parametrize("theme_id", BUNDLED)
def test_a_box_on_a_complex_widget_gives_its_arrow_column_back(presets, theme_id: str) -> None:
    """State a complex widget's box, give its arrows their room back.

    One ``border``, ``padding`` or ``background`` on a ``QSpinBox`` or a
    ``QComboBox`` makes ``QStyleSheetStyle`` compute ``SC_SpinBoxEditField`` from
    the box's own padding rect, which knows nothing about the arrows. It came back
    spanning the whole widget, so the line edit was laid over both arrow buttons:
    they painted, they looked right, and every click meant for them landed in the
    text field instead.

    Both a styled preset's input chrome and *any* preset's focus ring are such a
    statement, which is why this is checked for all of them and not just the styled
    ones. The correction is a ``padding-right`` the width of the arrow column,
    measured from the platform style rather than tokened — see
    :func:`mm_companion.ui.theme.arrow_columns`.
    """
    sheet = qss.build(presets[theme_id])

    for widget in COMPLEX_INPUTS:
        states_a_box = re.search(
            rf"(^|[,\s]){widget}\s*(:[\w-]+)?\s*(\{{|,)[^}}]*?"
            rf"(border|padding|background)\s*[-\w]*\s*:",
            sheet,
            re.MULTILINE | re.DOTALL,
        )
        if not states_a_box:
            continue
        assert re.search(rf"(^|[,\s]){widget}[^}}]*?padding-right\s*:", sheet, re.MULTILINE), (
            f"{theme_id} states a box on {widget} but never gives back a "
            f"padding-right, so its edit field is laid out over its own arrows"
        )


@pytest.mark.parametrize("theme_id", BUNDLED)
def test_the_arrow_column_is_measured_not_tokened(presets, theme_id: str) -> None:
    """How wide the arrows are is a fact about the *style*, not about the theme.

    50px under ``windows11``, 15px under ``Fusion`` — so a token would be wrong on
    three platforms out of four. ``build`` takes the measurement; the constants are
    only for a caller with no ``QApplication``.
    """
    default = qss.build(presets[theme_id])
    measured = qss.build(presets[theme_id], (44, 26))

    assert "padding-right: 44px" in measured
    assert "padding-right: 26px" in measured
    assert measured != default
