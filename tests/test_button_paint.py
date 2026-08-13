"""A tool button's label must be *readable* under every preset.

``test_theme_qss`` guards the rule (state a tool button's colour in both states).
This guards the consequence, by painting a real button and measuring the ink —
the ``test_menu_disabled_paint`` precedent, and for the same reason: the failure
is silent. Nothing raises and nothing looks broken in isolation; the caption
simply stops being there. It shipped that way: on Parchment a tool button's label
was painted white on cream, so every block title bar had all but lost its
pin/float/close glyphs, and on the dark presets a *checked* one had no visible
label at all.

Two things about how it is measured, both learned the hard way:

**The button is the Notes block's own, in a real sheet.** An equivalent button
built standalone comes out readable even with the rule taken back out — what
makes the difference is the cascade it really sits in, a widget-level stylesheet
on the enclosing group box inside a styled block frame. A fixture that skips that
measures a button the app does not have.

**This is style-dependent**, like ``test_input_arrow_columns`` and unlike
``test_menu_disabled_paint``: the colour Qt falls back to when the sheet states
none is the *platform style's*, so under ``offscreen`` the miss does not
reproduce and these assertions pass either way. They bite on a real style, which
is what CI gives them under xvfb.

The sibling half of this — a focus ring thicker than the border it replaces
clipping the caption it rings — is guarded only as a rule, in ``test_theme_qss``.
It is a two-pixel loss, which is below what counting ink can tell apart, and the
ring's own ink masks it; a paint test for it passed with the bug present, which
is worse than not having one.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import theme
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.theme import tokens

#: Only the styled presets. Classic states no chrome at all — the platform paints
#: its buttons, which is exactly what that preset is for.
STYLED = ["slate-dark", "parchment-light", "crimson-gold"]

#: What a label has to clear against the ground it sits on. The same floor the
#: semantic tints are held to in ``test_theme``.
MIN_LABEL_CONTRAST = tokens.MIN_CONTRAST


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def toolbar(qapp: QApplication):
    """A Notes block under one preset, laid out but never on screen.

    Hands the application look back afterwards, so a preset cannot leak into
    whatever test runs next.
    """
    previous = qapp.styleSheet()
    sheets: list[CharacterSheet] = []

    def dress(preset: str):
        theme.set_active_theme(preset)
        theme.apply(qapp)
        sheet = CharacterSheet(load_game_data())
        sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        # Every other block closed, so the Notes toolbar is at the top of the page
        # and inside the window's grab. On a full sheet it sits ~1700px down, past
        # anything a screenshot of the window can reach.
        for key in sheet.block_keys():
            if key != "notes":
                sheet.hide_block(key)
        sheet.resize(900, 400)
        sheet.show()
        for _ in range(10):
            qapp.processEvents()
        sheets.append(sheet)
        return sheet.notes

    yield dress
    for sheet in sheets:
        sheet.close()
        sheet.deleteLater()
    qapp.setStyleSheet(previous)
    theme.set_active_theme("classic")
    theme.apply(qapp)


def pixels(widget, qapp: QApplication) -> dict[str, int]:
    """Every colour painted inside *widget*, counted off its top-level window.

    Grabbed through the window so the surfaces beneath the button are really
    there to composite its translucent states against.
    """
    for _ in range(6):
        qapp.processEvents()
    window = widget.window()
    image = window.grab().toImage()
    ratio = image.width() / max(window.width(), 1)
    origin = widget.mapTo(window, widget.rect().topLeft())
    counts: dict[str, int] = {}
    for y in range(int(origin.y() * ratio), int((origin.y() + widget.height()) * ratio)):
        for x in range(int(origin.x() * ratio), int((origin.x() + widget.width()) * ratio)):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    return counts


def label_contrast(counts: dict[str, int]) -> tuple[str, str, float]:
    """The ground, the most contrasting ink on it, and the ratio between them."""
    ground = max(counts, key=counts.get)
    ink = max(counts, key=lambda name: tokens.contrast_ratio(name, ground))
    return ground, ink, tokens.contrast_ratio(ink, ground)


@pytest.mark.parametrize("theme_id", STYLED)
@pytest.mark.parametrize("checked", [False, True])
def test_a_tool_buttons_label_is_readable(
    qapp: QApplication, toolbar, theme_id: str, checked: bool
) -> None:
    notes = toolbar(theme_id)
    # Any reference will do — the toggle only needs a tab to act on, and a note
    # whose file is missing opens as an empty editor rather than raising.
    notes.open_note("unwritten.md")
    button = notes._preview_button
    button.setChecked(checked)

    ground, ink, ratio = label_contrast(pixels(button, qapp))

    assert ratio >= MIN_LABEL_CONTRAST, (
        f"{theme_id}: a {'checked' if checked else 'resting'} tool button's label "
        f"({ink}) on {ground} is {ratio:.2f}:1"
    )
