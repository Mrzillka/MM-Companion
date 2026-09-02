"""The divider between two blocks, and the detent it sticks at.

Every resize on the page happens by dragging one of these, so it has two jobs
that pull against each other. It has to be *easy to grab* — a hairline is not a
grab target, and the page is nothing but hairlines if the divider is drawn as
one — and it has to be *quiet*, because a page of visible gutters reads as a
spreadsheet rather than a character sheet. So it is `grid.handle` pixels wide,
painted as nothing at rest and a soft accent under the pointer.

The interesting half is the **detent**. A block's recommended size used to be a
floor the layout enforced; the grid lets anyone drag straight past it, which is
the point, but "you may" is not the same as "by accident". So the handle sticks
briefly at the recommended size and needs a deliberate extra pull to go by, and
while it is being dragged the splitter marks where those sizes are. Nothing is
forbidden and nothing is hidden, but you cannot cross the line without meaning
to, and you can always find your way back.

:func:`snap_to_detent` is the whole rule, kept out of Qt so it can be tested
directly — the same split :mod:`mm_companion.ui.reflow` and
:mod:`mm_companion.ui.sections.column_flow` make between a decision and the
widgets that act on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

from mm_companion.ui import theme

#: How much of the handle's thickness the hover mark paints down the middle.
_MARK_RATIO = 0.5


def snap_to_detent(position: int, targets: Sequence[int], strength: int) -> int:
    """*position*, pulled onto the nearest target within *strength* pixels.

    The dead-band is symmetric and the nearest target wins, so two recommended
    sizes closer together than the band cannot fight over the handle. A
    non-positive *strength* turns the detent off entirely, which is what a preset
    saying ``"grid.detent": 0`` means, and an empty *targets* leaves the position
    alone — a block with no recommendation has nothing to stick at.

    Note this pulls *towards* a target rather than refusing to leave one: the
    handle is never actually held, it just prefers the recommended size while the
    pointer is near it. Dragging on by more than the band is all it takes to go
    past, which is the "deliberate extra pull" without a mode or a modifier key.
    """
    if strength <= 0 or not targets:
        return position
    nearest = min(targets, key=lambda target: abs(target - position))
    return nearest if abs(nearest - position) <= strength else position


def paint_divider(
    widget: QWidget, painter: QPainter, hovered: bool, dragging: bool, *, horizontal: bool
) -> None:
    """Paint one divider: nothing at rest, a soft accent line under the pointer.

    Shared by both dividers on the page — the splitter handles inside a row and
    the grips between rows — because they are the same affordance and would read
    as two if they were drawn twice.

    Deliberately *not* Qt's own handle furniture, which draws a raised panel with
    dots down the middle. A dozen blocks would mean a dozen visible gutters, and
    the block frames already have edges: at rest a divider only has to be
    grabbable, not visible.
    """
    if not (hovered or dragging):
        return
    colour = QColor(theme.color("accent"))
    colour.setAlphaF(0.9 if dragging else 0.45)
    rect = widget.rect()
    if horizontal:
        inset = int(rect.width() * (1 - _MARK_RATIO) / 2)
        painter.fillRect(rect.adjusted(inset, 0, -inset, 0), colour)
    else:
        inset = int(rect.height() * (1 - _MARK_RATIO) / 2)
        painter.fillRect(rect.adjusted(0, inset, 0, -inset), colour)


class DetentHost(Protocol):
    """What a :class:`GridHandle` needs from the splitter it belongs to."""

    def detent_positions(self, index: int) -> list[int]: ...
    def mark_detents(self, index: int, targets: Sequence[int], settled: int | None) -> None: ...
    def clear_detent_marks(self) -> None: ...


class GridHandle(QSplitterHandle):
    """One draggable divider, with the recommended-size detent.

    Qt's own handle drags by calling ``moveSplitter`` with the pointer's position
    every move; this does the same with :func:`snap_to_detent` in between, which
    is the smallest possible place to put the behaviour. ``closestLegalPosition``
    still has the last word, so a detent can never push a handle somewhere the
    splitter would not allow.
    """

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self._press_offset = 0
        self._hovered = False
        self._dragging = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    # -- geometry -------------------------------------------------------------

    @property
    def _index(self) -> int:
        """Which handle of the splitter this is. Handle *n* precedes widget *n*."""
        return self.splitter().indexOf(self)

    def _along(self, point: QPoint) -> int:
        """The pointer's position along the axis this handle divides."""
        return point.y() if self.orientation() == Qt.Orientation.Vertical else point.x()

    def _targets(self) -> list[int]:
        host = self.splitter()
        positions = getattr(host, "detent_positions", None)
        return list(positions(self._index)) if callable(positions) else []

    # -- the drag -------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        # Where inside the handle it was grabbed, so it does not jump under the
        # pointer on the first move.
        self._press_offset = self._along(event.position().toPoint())
        self._dragging = True
        targets = self._targets()
        marker = getattr(self.splitter(), "mark_detents", None)
        if callable(marker):
            marker(self._index, targets, None)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if not self._dragging or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        parent = self.parentWidget()
        if parent is None:
            return
        local = parent.mapFromGlobal(event.globalPosition().toPoint())
        wanted = self._along(local) - self._press_offset
        targets = self._targets()
        snapped = snap_to_detent(wanted, targets, int(theme.metric("grid.detent")))
        legal = self.closestLegalPosition(snapped, self._index)
        marker = getattr(self.splitter(), "mark_detents", None)
        if callable(marker):
            marker(self._index, targets, legal if legal == snapped != wanted else None)
        self.moveSplitter(legal)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            clear = getattr(self.splitter(), "clear_detent_marks", None)
            if callable(clear):
                clear()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- painting -------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        del event
        paint_divider(
            self,
            QPainter(self),
            self._hovered,
            self._dragging,
            horizontal=self.orientation() == Qt.Orientation.Horizontal,
        )


def handle_thickness() -> int:
    """How wide every divider on the page is, from the theme."""
    return max(1, int(theme.metric("grid.handle")))
