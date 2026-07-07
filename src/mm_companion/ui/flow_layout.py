"""A layout that arranges its items left-to-right and wraps to new lines.

Qt ships no wrapping layout out of the box; this is the standard flow-layout
pattern, used here to lay out a variable number of condition chips.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowContainer(QWidget):
    """A host widget for a :class:`FlowLayout` that reports its wrapped height.

    A plain ``QWidget`` does not advertise height-for-width to its parent layout, so
    a vertical parent allocates it only its (single-row) size hint — and the wrapped
    chip rows then paint past it and overlap whatever sits below. This subclass turns
    height-for-width on and pins its ``minimumHeight`` to the flow's height at the
    current width, so the enclosing block grows to fit every row instead.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        layout = self.layout()
        return layout.heightForWidth(width) if layout is not None else super().heightForWidth(width)

    def resizeEvent(self, event: QResizeEvent) -> None:
        layout = self.layout()
        if layout is not None and self.width() > 0:
            self.setMinimumHeight(layout.heightForWidth(self.width()))
        super().resizeEvent(event)


class FlowLayout(QLayout):
    """Lays items out horizontally, wrapping to the next row when out of width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self.setSpacing(spacing)
        self._items: list[QLayoutItem] = []

    # -- QLayout plumbing -------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    # -- layout logic -----------------------------------------------------
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
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
