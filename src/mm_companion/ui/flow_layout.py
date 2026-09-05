"""A layout that arranges its items left-to-right and wraps to new lines.

Qt ships no wrapping layout out of the box; this is the standard flow-layout
pattern, used here to lay out a variable number of condition chips.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowContainer(QWidget):
    """A host widget for a :class:`FlowLayout` that reports its wrapped height.

    A plain ``QWidget`` reports only its (single-row) size hint to a vertical parent
    layout, and the flow's wrapped rows then paint past it, overlapping whatever sits
    below. This subclass pins its ``minimumHeight`` to the flow's height at the width
    it actually has, so the enclosing block grows to fit every row instead.

    Deliberately *not* by advertising height-for-width. Qt asks a height-for-width
    widget how tall it would be at its parent's preferred **hint** width, not at the
    width it is really given — for a flow that is one item's width, so it answers
    with every item in its own row. That inflated figure becomes the enclosing
    block's minimum height, and the surplus goes to whatever else shares the page:
    two NPC cards side by side would claim the height of two rows, and the Players
    block above them would grow to absorb it.

    The pin has to be re-taken whenever the flow re-wraps, and it will not notice on
    its own. A width change arrives as a resize; a change in the items, or in one
    item's size, does not — the widget wants to get *shorter*, and the stale pin is
    exactly what stops it, so no resize ever fires and the pin is never recomputed.
    Left at that the container ratchets: permanently as tall as the most it has ever
    held, with the block around it never giving the space back. Hence the two other
    ways in — :class:`FlowLayout` calls :meth:`refresh_height` as items come and go,
    and a layout request (a child resizing itself) re-takes it too.
    """

    #: The container was given a different width. For a host that has to re-size
    #: the *items* to suit — the GM's card grids give every card one shared width,
    #: and what that width can be depends on how much room the block has. A flow
    #: re-wraps on its own; anything that has to re-measure cannot.
    widthChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self.setSizePolicy(policy)
        self._row_height = 0

    def set_minimum_row_height(self, height: int) -> None:
        """Keep the container at least *height* tall even when the flow is shorter.

        For a container that stays visible while empty, so its row doesn't collapse
        to nothing between renders.
        """
        self._row_height = max(0, height)
        self.refresh_height()

    def heightForWidth(self, width: int) -> int:
        """How tall the flow wraps to at *width* — asked for explicitly, not by Qt.

        The layout reports no height-for-width (see the class docstring), so Qt
        never calls this; it is here for callers that know the width they mean.
        """
        layout = self.layout()
        if layout is None:
            return super().heightForWidth(width)
        return max(self._row_height, layout.heightForWidth(width))

    def refresh_height(self) -> None:
        """Re-pin the minimum height to what the flow needs at the current width."""
        if self.layout() is None or self.width() <= 0:
            return
        height = self.heightForWidth(self.width())
        if height != self.minimumHeight():
            self.setMinimumHeight(height)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.refresh_height()
        if event.oldSize().width() != event.size().width():
            self.widthChanged.emit()
        super().resizeEvent(event)

    def event(self, event: QEvent) -> bool:
        # An item that merely changed *size* re-wraps the flow just as much as one
        # that came or went — an NPC card losing a condition chip, say. Qt tells us
        # about that with a layout request and nothing else, since our own width is
        # untouched and no resize follows.
        if event.type() == QEvent.Type.LayoutRequest:
            self.refresh_height()
        return super().event(event)


class FlowLayout(QLayout):
    """Lays items out horizontally, wrapping to the next row when out of width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self.setSpacing(spacing)
        self._items: list[QLayoutItem] = []

    # -- QLayout plumbing -------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
        self._contents_changed()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self._contents_changed()
            return item
        return None

    def _contents_changed(self) -> None:
        """A different set of items wraps into a different number of rows.

        Nothing else notices: the container's width has not changed, so it gets no
        resize, and its host has no reason to re-measure it. Both this layout's
        cached sizes and the container's pinned height are told directly instead.
        """
        self.invalidate()
        container = self.parentWidget()
        if isinstance(container, FlowContainer):
            container.refresh_height()

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        """No — on purpose, even though :meth:`heightForWidth` is implemented.

        Saying yes here (a widget inherits this answer from its layout) makes Qt
        size the container by asking how tall it would be at the *hint* width — a
        single item's width, so every item in its own row — rather than at the width
        it is really given. See :class:`FlowContainer`, which pins its height from
        its actual width instead.
        """
        return False

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        if not self._items:
            return QSize(0, 0)  # an empty flow takes no space, not just its margins
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    # -- layout logic -----------------------------------------------------
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        if not self._items:
            return 0  # an empty flow takes no height, not just its margins
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()
