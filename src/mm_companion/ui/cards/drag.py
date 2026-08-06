"""The grip a card is picked up by, and the drag payload it carries."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from mm_companion.ui.widgets import muted_style

#: Drag-and-drop payload: the dragged node's stable id (a ``Power.id`` or
#: ``PowerGroup.id``). A tree position needs parent context, not a bare index, so
#: drops resolve the id.
#:
#: Every widget in this package takes the format as a parameter defaulting to this
#: one, so a second block built on the same cards (equipment) names its own and the
#: two boards cannot accept each other's drags.
NODE_MIME = "application/x-mm-power-node"


class DragHandle(QLabel):
    """The ``⠿`` grip at the head of a card; a press-drag on it starts the drag.

    It only *detects* the gesture (a left-press moved past the platform drag
    threshold) and emits :attr:`dragStarted`; the owning card builds and runs the
    actual :class:`QDrag`, so the grip stays a dumb, reusable handle.
    """

    dragStarted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("⠿", parent)
        self._press: QPoint | None = None
        self.setToolTip("Drag to reorder, or drop onto another power to group them")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(muted_style())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        moved = (event.position().toPoint() - self._press).manhattanLength()
        if moved >= QApplication.startDragDistance():
            self._press = None
            self.dragStarted.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: ARG002
        self._press = None
