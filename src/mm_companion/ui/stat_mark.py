"""The small round dot beside a stat a temporary condition is moving.

A temporary condition — an All-Out Attack's −3 Defense, Defending's +2 — lasts until
the start of its bearer's next turn and is removed by hand, so the sheet has to say
which numbers are only borrowed. Every stat it touches gets a dot: green when the
temporary conditions leave it higher, red when lower, and the tooltip names them.
What a stat's mark *is* comes from :func:`~mm_companion.core.rules.temporary_marks`;
this module only draws it, in two forms:

- :class:`StatMark`, a widget for a readout built of labels (a power card's attack
  line, a simple-sheet box). It is **hidden, never removed**, while there is no mark,
  and keeps its room while hidden, so a row does not jump when a dot comes and goes.
- :class:`StatMarkDelegate`, for a table cell (the Resistances and Skills totals): the
  dot is painted into the cell's top-right corner over its ordinary contents, so it
  costs the column no width and moves no text. A cell is marked by
  :func:`mark_item`, which writes :data:`MARK_ROLE`.

Both colour the dot from the theme's ``tint.better`` / ``tint.worse`` and size it from
``mark.size``, read at paint time so a preset switch reaches them.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidgetItem,
    QWidget,
)

from mm_companion.core.rules import MARK_BETTER, TemporaryMark
from mm_companion.ui import theme
from mm_companion.ui.paint import painting

#: Where a table cell keeps its mark's tone (``"better"`` / ``"worse"``), for
#: :class:`StatMarkDelegate` to paint. Clear of ``ROLL_ROLE`` (``UserRole``) and the pin
#: picker's ``UserRole + 1``.
MARK_ROLE = Qt.ItemDataRole.UserRole + 64


def mark_token(tone: str) -> str:
    """The colour token a mark of *tone* is drawn in."""
    return "tint.better" if tone == MARK_BETTER else "tint.worse"


def paint_mark(painter: QPainter, rect: QRect, tone: str) -> None:
    """Draw one dot of *tone*, filling a square of ``mark.size`` centred in *rect*."""
    size = int(theme.metric("mark.size"))
    if size <= 0:
        return
    left = rect.x() + (rect.width() - size) // 2
    top = rect.y() + (rect.height() - size) // 2
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(theme.color(mark_token(tone)))))
    painter.drawEllipse(left, top, size, size)
    painter.restore()


class StatMark(QWidget):
    """One stat's dot, as a widget: shown while a temporary condition moves the stat.

    Sized to the dot plus a hair of room either side, and fixed there: it is a mark,
    and a mark that stretched would push the number it belongs to around.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = ""
        policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Hidden is "no mark", not "no room": the row it sits in keeps its shape.
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.hide()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        side = int(theme.metric("mark.size")) + 2 * int(theme.metric("space.xxs"))
        return QSize(side, side)

    @property
    def tone(self) -> str:
        """``"better"``, ``"worse"``, or ``""`` while there is no mark."""
        return self._tone

    def set_mark(self, mark: TemporaryMark | None) -> None:
        """Show *mark*, or hide the dot when there is none."""
        self._tone = mark.tone if mark is not None else ""
        self.setToolTip(mark.tooltip if mark is not None else "")
        self.setVisible(mark is not None)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._tone:
            return
        with painting(self) as painter:
            paint_mark(painter, self.rect(), self._tone)


def mark_item(item: QTableWidgetItem, mark: TemporaryMark | None) -> None:
    """Mark a table cell for :class:`StatMarkDelegate`, and say why in its tooltip.

    Call it *after* whatever writes the cell's tooltip (``paint_stat_cell``): the mark's
    line is added to what is there, never in place of it.
    """
    item.setData(MARK_ROLE, mark.tone if mark is not None else None)
    if mark is None:
        return
    tip = item.toolTip()
    item.setToolTip(f"{tip}\n{mark.tooltip}" if tip else mark.tooltip)


class StatMarkDelegate(QStyledItemDelegate):
    """Paints a cell as usual, then its mark's dot in the cell's top-right corner.

    Installed on a table's Total column (``setItemDelegateForColumn``). A cell with no
    :data:`MARK_ROLE` paints exactly as it did without the delegate.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)
        tone = index.data(MARK_ROLE)
        if not tone:
            return
        size = int(theme.metric("mark.size"))
        inset = int(theme.metric("space.xs"))
        rect = option.rect
        corner = QRect(rect.right() - inset - size + 1, rect.top() + inset, size, size)
        paint_mark(painter, corner, str(tone))


def install_mark_delegate(table, column: int) -> StatMarkDelegate:
    """Give *table*'s *column* a :class:`StatMarkDelegate`, parented to the table."""
    delegate = StatMarkDelegate(table)
    table.setItemDelegateForColumn(column, delegate)
    return delegate
