from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui.power_constructor.common import CHIP_MIME, brick_tooltip
from mm_companion.ui.power_constructor.modifier_chip import ModifierChip
from mm_companion.ui.theme import TINT_WORSE, tint_rgba


class BrickWidget(QFrame):
    """A draggable palette brick: a name and its cost text, carrying a record id.

    On drag it starts a :class:`QDrag` whose mime data holds the record id in the
    given format (``EFFECT_MIME`` or ``MODIFIER_MIME``), so drop targets know both
    what kind of brick it is and which record it refers to.

    The brick shows only a name and a cost; the record's ``description`` is its
    tooltip, so what an effect or modifier actually *does* is one hover away without
    the palette turning into a wall of prose.
    """

    def __init__(
        self,
        title: str,
        subtitle: str,
        mime: str,
        payload: str,
        *,
        flat: bool = False,
        description: str = "",
    ) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._mime = mime
        self._payload = payload
        self._press_pos = None
        self.setToolTip(brick_tooltip(title, subtitle, description))
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
            # Wrap rather than clip — the longer formulas ("-1 point per rank flat")
            # otherwise run past the palette's edge.
            cost.setWordWrap(True)
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


class PaletteDropZone(QWidget):
    """The palette column, doubling as the discard target for an attached chip.

    Dragging a modifier chip off its effect card and dropping it back on the palette
    detaches it — the exact reverse of the drag that added it, so removing no longer
    means hitting the chip's small ``✕``. It accepts a chip drag only (a palette brick
    dragged around inside the palette is not a removal) and washes itself in the flaw
    red while one hovers, so the gesture reads as "throw it back" rather than a drop
    that will be ignored.

    Removal runs through the chip's own :attr:`~ModifierChip.removeRequested` signal,
    which its effect card is already connected to, so the drop reuses the one removal
    path instead of adding a second.
    """

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content)
        self._hint = QLabel("Drop to remove", self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._hint.setStyleSheet(
            f"background: {tint_rgba(TINT_WORSE, 0.35)}; color: white;"
            f" font-weight: bold; border: 2px solid {TINT_WORSE}; border-radius: 6px;"
        )
        self._hint.hide()

    @staticmethod
    def _accepts(event) -> bool:
        return event.mimeData().hasFormat(CHIP_MIME) and isinstance(event.source(), ModifierChip)

    def _show_hint(self, visible: bool) -> None:
        if visible:
            self._hint.setGeometry(self.rect())
            self._hint.raise_()
        self._hint.setVisible(visible)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._accepts(event):
            self._show_hint(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._show_hint(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._show_hint(False)
        chip = event.source()
        if not isinstance(chip, ModifierChip):
            event.ignore()
            return
        self.remove_chip(chip)
        event.acceptProposedAction()

    @staticmethod
    def remove_chip(chip: ModifierChip) -> None:
        """Detach ``chip`` from its effect (the seam the drop handler delegates to).

        Deferred by a zero timer: the drop is delivered from inside the chip's own
        ``drag.exec()``, and the card's removal deletes the chip widget — which is
        still mid-``mouseMoveEvent``. Letting that unwind first keeps the gesture from
        pulling the widget out from under the event that started it.
        """
        QTimer.singleShot(0, lambda: chip.removeRequested.emit(chip))
