"""The wheel guard should only let a focused spin box react to the wheel."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wheel(widget) -> QWheelEvent:
    return QWheelEvent(
        QPointF(1, 1),
        widget.mapToGlobal(QPoint(1, 1)),
        QPoint(0, -120),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_unfocused_spin_box_ignores_wheel(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    spin = next(iter(sheet.stats._abilities.values()))
    spin.setValue(5)

    QApplication.sendEvent(spin, _wheel(spin))
    assert spin.value() == 5


def test_focused_spin_box_reacts_to_wheel(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    spin = next(iter(sheet.stats._abilities.values()))
    spin.setValue(5)

    sheet.show()
    sheet.activateWindow()
    spin.setFocus()
    QApplication.processEvents()
    if not spin.hasFocus():
        pytest.skip("environment cannot give the spin box keyboard focus")

    QApplication.sendEvent(spin, _wheel(spin))
    assert spin.value() != 5
