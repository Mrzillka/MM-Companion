from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)


class BrickWidget(QFrame):
    """A draggable palette brick: a name and its cost text, carrying a record id.

    On drag it starts a :class:`QDrag` whose mime data holds the record id in the
    given format (``EFFECT_MIME`` or ``MODIFIER_MIME``), so drop targets know both
    what kind of brick it is and which record it refers to.
    """

    def __init__(
        self, title: str, subtitle: str, mime: str, payload: str, *, flat: bool = False
    ) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._mime = mime
        self._payload = payload
        self._press_pos = None
        # The palette search box matches on the name only — the cost subtitle
        # ("1 per rank", …) is the same across most bricks and would swamp
        # single-letter queries with matches.
        self.search_key = title.lower()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(1)

        header = QHBoxLayout()
        header.setSpacing(4)
        name = QLabel(title)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        header.addStretch()
        if flat:
            # A flat modifier costs a one-time add/subtract rather than per rank.
            badge = QLabel("flat")
            badge.setStyleSheet(
                "background: #555; color: white; border-radius: 4px; padding: 0 4px;"
            )
            header.addWidget(badge)
        layout.addLayout(header)

        if subtitle:
            cost = QLabel(subtitle)
            cost.setEnabled(False)
            layout.addWidget(cost)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None:
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._mime, self._payload.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._press_pos = None
