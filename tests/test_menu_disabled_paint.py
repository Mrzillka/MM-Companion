"""A disabled menu-bar action must *look* disabled under every preset.

``test_theme_qss`` guards the rule (state a menu's colour, restate its disabled
one). This file guards the consequence, by painting the bar and measuring the ink.

The trap is Qt's all-or-nothing box model again, the same one
``test_input_arrow_columns`` covers for spin boxes: the moment the application
stylesheet states a flat ``color`` on ``QMenuBar``, ``QStyleSheetStyle`` stops
painting that property's states, and a disabled action comes out at full strength.
Nothing raises, nothing looks broken in isolation — the button simply stops saying
whether it will do anything. It shipped that way for the Undo and Redo buttons on
an empty history, which is exactly when a user first meets them.

Unlike the arrow-column tests this is not style-dependent: the stylesheet colour is
painted by ``QStyleSheetStyle`` on every platform, so these assertions are worth
the same on CI as on Windows.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.ui import theme
from mm_companion.ui.main_window import MainWindow

BUNDLED = ["classic", "slate-dark", "parchment-light", "crimson-gold"]


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dressed(qapp: QApplication):
    """Render under one preset, and hand the application look back afterwards."""
    previous = qapp.styleSheet()

    def dress(preset: str) -> None:
        theme.set_active_theme(preset)
        theme.reset()
        theme.apply(qapp)

    yield dress
    qapp.setStyleSheet(previous)


def _ink(bar, action) -> float:
    """How much the action's rect differs from the bar's background, per pixel.

    A crude but honest reading of "how strongly is this painted": the glyph is the
    only thing in the rect, so less ink is a fainter glyph and 0 would be nothing
    drawn at all.
    """
    image = bar.grab(bar.actionGeometry(action)).toImage()
    background = image.pixelColor(0, 0)
    total = 0
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            total += (
                abs(colour.red() - background.red())
                + abs(colour.green() - background.green())
                + abs(colour.blue() - background.blue())
            )
    return total / max(1, image.width() * image.height())


@pytest.mark.parametrize("preset", BUNDLED)
def test_a_disabled_undo_button_paints_fainter_than_a_live_one(
    qapp: QApplication, dressed, preset: str
) -> None:
    window = MainWindow(locked=False)
    dressed(preset)
    window.show()
    qapp.processEvents()

    assert not window._undo_action.isEnabled()
    disabled = _ink(window.menuBar(), window._undo_action)

    window.sheet.abilities._abilities["STR"].setValue(4)
    qapp.processEvents()
    assert window._undo_action.isEnabled()
    live = _ink(window.menuBar(), window._undo_action)

    window._dirty = False  # a "save your changes?" modal would hang the teardown
    assert disabled < live * 0.9, (
        f"{preset} paints a disabled Undo button at {disabled:.2f} ink against a live "
        f"{live:.2f} — a button that looks live but does nothing reads as broken"
    )
