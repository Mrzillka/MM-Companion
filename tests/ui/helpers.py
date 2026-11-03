"""Helpers more than one UI test file drives widgets with."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget


def real_double_click(qapp: QApplication, widget: QWidget) -> None:
    """What a real double-click delivers: press, release, double-click, release.

    ``QTest.mouseDClick`` sends the double-click alone, which is exactly the
    sequence that hid a bug: the release ending the gesture loaded the roller's chip
    straight back after the roll had emptied it.
    """
    local = QPointF(5, 5)
    button, no_mods = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    for kind, held in (
        (QEvent.Type.MouseButtonPress, button),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
        (QEvent.Type.MouseButtonDblClick, button),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        global_pos = QPointF(widget.mapToGlobal(local.toPoint()))
        event = QMouseEvent(kind, local, global_pos, button, held, no_mods)
        QApplication.sendEvent(widget, event)
        qapp.processEvents()
