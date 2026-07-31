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

from mm_companion.ui import theme
from mm_companion.ui.power_constructor.common import _GROUP_HEADER, CHIP_MIME, brick_tooltip
from mm_companion.ui.power_constructor.modifier_chip import ModifierChip
from mm_companion.ui.widgets import BOLD_STYLE


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
        name.setStyleSheet(BOLD_STYLE)
        header.addWidget(name)
        header.addStretch()
        if flat:
            # A flat modifier costs a one-time add/subtract rather than per rank.
            badge = QLabel("flat")
            badge.setStyleSheet(
                f"background: {theme.color('badge.flat')};"
                f" color: {theme.color('text.on-badge')};"
                f" border-radius: {int(theme.metric('radius.header'))}px;"
                f" padding: 0 {int(theme.metric('space.sm'))}px;"
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
        # The left button has to still be down: without this a later right- or
        # middle-drag across the brick would start a left-button drag from a press
        # point left over from an earlier click.
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
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

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Clear the press point, so a click that never became a drag leaves none behind."""
        self._press_pos = None
        super().mouseReleaseEvent(event)


class BrickList(QWidget):
    """The scrollable body of a palette tab: bricks, optionally under group headers.

    Owns everything the tab's search box and sort switch used to have threaded
    through them as parallel arguments — the layout, the sections, the flat brick
    list and the "No matches" note — and exposes the two operations over them:

    - :meth:`filter_to` hides the bricks that don't match a query, along with any
      section header left with nothing visible.
    - :meth:`set_alphabetical` swaps between the grouped layout and one flat A-Z
      list, re-applying the current filter so a query survives the toggle.

    A flat tab (extras, flaws) is just the degenerate case: one unnamed section.
    """

    def __init__(
        self,
        groups: list[tuple[str | None, list[BrickWidget]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(4)

        self._sections: list[tuple[QLabel | None, list[BrickWidget]]] = []
        self.bricks: list[BrickWidget] = []
        self._alpha = False
        self._query = ""

        for title, group in groups:
            header = self._build_header(title) if title is not None else None
            if header is not None:
                self._layout.addWidget(header)
            for brick in group:
                self._layout.addWidget(brick)
            self._sections.append((header, group))
            self.bricks.extend(group)

        self._empty = QLabel("No matches")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setEnabled(False)
        self._empty.setVisible(False)
        self._layout.addWidget(self._empty)
        self._layout.addStretch()

    @staticmethod
    def _build_header(title: str) -> QLabel:
        header = QLabel(title)
        # Named so a header is addressable as one: a brick can share a group's name
        # (the Attack extra vs. the Attack effect group), so selecting headers by
        # their text alone would sweep bricks up too.
        header.setObjectName(_GROUP_HEADER)
        header.setStyleSheet(
            f"font-weight: bold; color: {theme.color('text.muted')};"
            f" padding-top: {int(theme.metric('space.sm'))}px;"
        )
        return header

    def filter_to(self, text: str) -> None:
        """Show only the bricks whose name contains *text* (case-insensitive)."""

        self._query = text
        needle = text.strip().lower()
        for brick in self.bricks:
            brick.setVisible(needle in brick.search_key)
        # In the flat A-Z view the section headers stay hidden regardless of matches.
        for header, group in self._sections:
            if header is not None:
                header.setVisible(not self._alpha and any(not b.isHidden() for b in group))
        self._empty.setVisible(all(b.isHidden() for b in self.bricks))

    def set_alphabetical(self, alpha: bool) -> None:
        """Lay the bricks out in one flat A-Z list, or back under their group headers.

        Headers and bricks are detached and re-inserted in the new order; the "No
        matches" note and the trailing stretch stay put at the end.
        """

        self._alpha = alpha
        for header, group in self._sections:
            if header is not None:
                self._layout.removeWidget(header)
            for brick in group:
                self._layout.removeWidget(brick)

        at = 0
        if alpha:
            for header, _group in self._sections:
                if header is not None:
                    header.setVisible(False)
            for brick in sorted(self.bricks, key=lambda b: b.search_key):
                self._layout.insertWidget(at, brick)
                at += 1
        else:
            for header, group in self._sections:
                if header is not None:
                    header.setVisible(True)
                    self._layout.insertWidget(at, header)
                    at += 1
                for brick in group:
                    self._layout.insertWidget(at, brick)
                    at += 1
        self.filter_to(self._query)


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
            f"background: {theme.wash('tint.worse', 0.35)};"
            f" color: {theme.color('text.on-badge')}; font-weight: bold;"
            f" border: {int(theme.metric('border.width.emphasis'))}px solid"
            f" {theme.color('tint.worse')};"
            f" border-radius: {int(theme.metric('radius.chip'))}px;"
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
            # Deliberately silent, unlike the other drop targets. What gets refused
            # here is an *effect* brick dragged back over the palette it came from —
            # a cancelled drag, not a mistake, and flashing a reject outline over the
            # drag's own origin would read as an error where none was made.
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
