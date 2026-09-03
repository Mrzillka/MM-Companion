"""Drawing a :mod:`~mm_companion.ui.layout_tree` node as real widgets.

The tree says what the arrangement *is*; this says what it looks like. Two
containers do all of it, and the difference between them is the whole of the
"width tiles, height scrolls" bargain:

* :class:`GridSplitter` renders a :class:`~mm_companion.ui.layout_tree.Split`
  **inside** a row. Its children share a fixed extent, so a divider drag here is
  zero-sum: give one block width and its neighbour loses exactly that much. This
  is a plain ``QSplitter`` with our own handle in it, because a splitter is
  already the right thing and reimplementing one would only be a worse one.

* :class:`RowStack` renders the **page**: the root vertical split, whose children
  are the rows. Its sizes are *absolute*, not shares — the rows may total more
  than the viewport, and the page scrolls. A drag here therefore cannot be
  zero-sum: pulling the divider under row 2 down makes row 2 taller and pushes
  everything below it down, rather than stealing from row 3. That is why the page
  is not simply another ``QSplitter``: a splitter divides a fixed total, and the
  page has no fixed total to divide.

A row nobody has dragged states a height of **zero**, which means "be as tall as
your content" — so the sheet goes on behaving exactly as it always has until
somebody actually resizes something, and adding a skill still makes the Skills
block taller rather than making it scroll inside a height nobody chose.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSizePolicy, QSplitter, QVBoxLayout, QWidget

from mm_companion.ui import theme
from mm_companion.ui.grid_handle import (
    GridHandle,
    handle_thickness,
    paint_divider,
    snap_to_detent,
)
from mm_companion.ui.layout_tree import HORIZONTAL, Leaf, Node
from mm_companion.ui.widgets import discard_widget

#: Builds the widget for one leaf. The canvas supplies this: a leaf naming one
#: block is that block's frame, and one naming several is a tab group.
LeafBuilder = Callable[[Leaf], QWidget]


def _recommended_extent(widget: QWidget, horizontal: bool) -> int:
    """The size *widget* would like along one axis, for the detent to stick at.

    A frame answers with its block's recommendation; anything else (a nested
    split, a tab group) answers with its own hint, which is the same question one
    level up. Zero means "no opinion", and a detent with no target does nothing.
    """
    recommend = getattr(widget, "recommended_size", None)
    if callable(recommend):
        size = recommend()
        stated = size.width if horizontal else size.height
        if stated:
            return int(stated)
    hint = widget.sizeHint()
    return hint.width() if horizontal else hint.height()


class GridSplitter(QSplitter):
    """One split inside a row: children sharing a fixed extent, zero-sum.

    Supplies the three things :class:`~mm_companion.ui.grid_handle.GridHandle`
    asks of the splitter it belongs to — where the recommended sizes are, and how
    to mark and unmark them — and nothing else. Everything about *what* is in it
    belongs to the canvas.
    """

    #: A handle drag settled. The canvas listens so it can record the new sizes.
    sizesSettled = Signal()
    #: One pane has been dragged small enough that letting go would close what is
    #: in it — or has been dragged back out of that. ``(widget, closing)``.
    paneCollapsing = Signal(object, bool)
    #: The handle was let go with these panes still that small. A list and one
    #: emission, because closing the first would relayout the page and leave the
    #: rest of them pointing at widgets that no longer exist.
    paneCollapsed = Signal(list)

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setObjectName("gridSplitter")
        self.setHandleWidth(handle_thickness())
        # The old page forbade this, because a squashed block meant a clipped one.
        # A block scrolls inside itself now, so collapsing one to nothing is a
        # thing somebody may legitimately want and nothing is lost by allowing it.
        self.setChildrenCollapsible(True)
        self.setOpaqueResize(True)
        # NOT parented here: a QSplitter adopts every child widget into its own
        # list of panes, so an overlay made a child of one becomes a pane of it —
        # which put an invisible strip of nothing at the left of every row. The
        # mark is built on first use, against the nearest ancestor that is not
        # itself a splitter. See :meth:`_mark_host`.
        self._mark: _DetentMark | None = None
        # The panes currently warned as too small to keep (see below).
        self._collapsing: list[QWidget] = []
        self.splitterMoved.connect(lambda *_: self.sizesSettled.emit())

    def createHandle(self) -> GridHandle:  # noqa: N802 - Qt override
        return GridHandle(self.orientation(), self)

    # -- what the handle asks for --------------------------------------------

    def detent_positions(self, index: int) -> list[int]:
        """Where handle *index* sits when the block on either side is happy.

        Two answers, not one: the block *before* the handle at its recommended
        size, and the block *after* it at that block's. Both are worth sticking
        at, and offering both is why dragging a divider between two blocks lets
        you settle either of them without going round the other side.
        """
        sizes = self.sizes()
        if not 0 < index < len(sizes):
            return []
        horizontal = self.orientation() == Qt.Orientation.Horizontal
        gap = self.handleWidth()
        before = sum(sizes[: index - 1]) + gap * (index - 1)
        after = sum(sizes) + gap * (len(sizes) - 1)
        trailing = sum(sizes[index + 1 :]) + gap * max(0, len(sizes) - index - 1)

        targets: list[int] = []
        leading_widget = self.widget(index - 1)
        if leading_widget is not None:
            wanted = _recommended_extent(leading_widget, horizontal)
            if wanted > 0:
                targets.append(before + wanted)
        trailing_widget = self.widget(index)
        if trailing_widget is not None:
            wanted = _recommended_extent(trailing_widget, horizontal)
            if wanted > 0:
                targets.append(after - trailing - gap - wanted)
        return [target for target in targets if target >= 0]

    def update_collapse_marks(self) -> None:
        """Say which panes a release would close, while the handle is still held.

        Collapsing a pane to nothing is allowed — that is the bargain of a grid
        the user owns — but a block two pixels wide is not something anybody can
        read, find or drag back open, so the honest reading of the drag is that
        they are getting rid of it. Warning while the mouse is down turns that
        from a surprise into a choice, and the block is closed rather than
        destroyed, so the worst case is one Ctrl+Z.

        Only a *drag* ever asks this. A relayout that happens to hand a pane a
        tiny size — a restored arrangement, a strip mid-rebuild — must never
        close anything, which is why this is called from the handle and not from
        anywhere that merely notices a size.
        """
        limit = int(theme.metric("grid.close-extent"))
        wanted: list[QWidget] = []
        if limit > 0:
            for index, size in enumerate(self.sizes()):
                widget = self.widget(index)
                if widget is not None and size < limit:
                    wanted.append(widget)
        for widget in self._collapsing:
            if widget not in wanted:
                self.paneCollapsing.emit(widget, False)
        for widget in wanted:
            if widget not in self._collapsing:
                self.paneCollapsing.emit(widget, True)
        self._collapsing = wanted

    def commit_collapse(self) -> None:
        """The handle was let go: close whatever it left too small to see."""
        collapsing, self._collapsing = self._collapsing, []
        for widget in collapsing:
            self.paneCollapsing.emit(widget, False)
        if collapsing:
            self.paneCollapsed.emit(collapsing)

    def mark_detents(self, index: int, targets: Sequence[int], settled: int | None) -> None:
        """Show where the recommended sizes are while a handle is being dragged."""
        del index
        mark = self._ensure_mark()
        if mark is None:
            return
        host = mark.parentWidget()
        mark.setGeometry(QRect(self.mapTo(host, QPoint(0, 0)), self.size()))
        mark.show_targets(targets, settled, self.orientation() == Qt.Orientation.Horizontal)

    def clear_detent_marks(self) -> None:
        """Take the mark down and get rid of it.

        Destroyed rather than hidden. The mark cannot be a child of the splitter
        it belongs to — a ``QSplitter`` adopts every child widget as a *pane* — so
        it lives on the nearest ancestor that is not one, and that ancestor
        routinely outlives the splitter (the pinned strip's host does, across every
        rebuild). A hidden mark left behind there is a widget nothing will ever
        collect. It only exists while a handle is being dragged, so making it live
        exactly that long is both simpler and leak-free.
        """
        mark, self._mark = self._mark, None
        if mark is not None:
            discard_widget(mark)

    def _mark_host(self) -> QWidget | None:
        """The nearest ancestor that will not swallow a child widget as a pane."""
        parent = self.parentWidget()
        while isinstance(parent, QSplitter):
            parent = parent.parentWidget()
        return parent

    def _ensure_mark(self) -> _DetentMark | None:
        host = self._mark_host()
        if host is None:
            return None
        if self._mark is None or self._mark.parentWidget() is not host:
            if self._mark is not None:
                discard_widget(self._mark)
            self._mark = _DetentMark(host)
        return self._mark


class _DetentMark(QWidget):
    """The faint lines showing where the recommended sizes are, during a drag.

    An overlay rather than something painted into the splitter, so it costs
    nothing at all when nobody is dragging and cannot be confused for part of the
    layout. It is transparent to the mouse, which matters: the whole point is
    that it appears *under the pointer* mid-drag.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._targets: list[int] = []
        self._settled: int | None = None
        self._horizontal = True
        self.hide()  # never visible before it is parented and placed

    def show_targets(self, targets: Sequence[int], settled: int | None, horizontal: bool) -> None:
        self._targets = [int(target) for target in targets]
        self._settled = settled
        self._horizontal = horizontal
        if not self._targets:
            self.clear()
            return
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        self._targets = []
        self._settled = None
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        del event
        if not self._targets:
            return
        painter = QPainter(self)
        rect = self.rect()
        for target in self._targets:
            lit = self._settled is not None and abs(target - self._settled) <= 1
            colour = theme.color("drop.indicator" if lit else "text.muted.rich")
            band = (
                QRect(target, rect.top(), 1, rect.height())
                if self._horizontal
                else QRect(rect.left(), target, rect.width(), 1)
            )
            painter.fillRect(band, colour)


class RowGrip(QWidget):
    """The divider under one row of the page. Dragging it makes that row taller.

    Not a ``QSplitterHandle``, because the page is not a splitter: a splitter
    divides a fixed total between its children, and dragging one of its handles
    can only ever move space from one side to the other. The page has no fixed
    total — the rows may add up to more than the window, and it scrolls — so
    pulling this down has to make the row above it taller and push everything
    below it down. That is the one behaviour a splitter cannot give.
    """

    #: Asked, on press, for the row above's current height and the heights it
    #: reads well at. A grip that had to be *told* those beforehand is a grip that
    #: silently works from zero if anybody forgets — which is exactly what
    #: happened: nothing called the arming method, so every drag treated the row
    #: as 0px tall and set its height to however far the mouse had moved. Asking
    #: at the moment of the press cannot be forgotten and cannot go stale.
    armed = Signal()
    #: Emitted while dragging, with the height the row above should now have.
    heightDragged = Signal(int)
    #: Emitted while dragging, with the pointer's position on screen. The page
    #: listens so it can auto-scroll when the drag runs into the edge of the
    #: window — which is the only way to make a row taller than the room left
    #: below it without letting go and starting again.
    dragMoved = Signal(QPoint)
    #: The drag has been pulled onto one of the recommended heights, so the mark
    #: for it can light up — the same signal a splitter's handle sends its mark.
    detentReached = Signal(int)
    #: Emitted on release, once the drag has settled.
    dragFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rowGrip")
        self.setCursor(Qt.CursorShape.SplitVCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(handle_thickness())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hovered = False
        self._dragging = False
        self._press_y = 0
        self._start_height = 0
        self._targets: list[int] = []
        # Where the pointer was last seen, so :meth:`extend` can re-derive the
        # height from a drag the pointer is not currently moving.
        self._last_global = QPoint()

    @property
    def targets(self) -> list[int]:
        """The heights this grip will stick at."""
        return list(self._targets)

    def begin_from(self, height: int, targets: Sequence[int]) -> None:
        """Say what the row above is right now. Answered in response to :attr:`armed`."""
        self._start_height = int(height)
        self._targets = [int(target) for target in targets]

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._dragging = True
        self._last_global = event.globalPosition().toPoint()
        self._press_y = self._last_global.y()
        self._start_height = 0
        self._targets = []
        self.armed.emit()  # the stack answers with the row's height and detents
        if self._start_height <= 0:
            # Nobody answered. Refuse the drag rather than treating the row as
            # zero pixels tall and collapsing it to however far the mouse moves.
            self._dragging = False
            super().mousePressEvent(event)
            return
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        self._last_global = event.globalPosition().toPoint()
        self._restate_height()
        self.dragMoved.emit(self._last_global)
        event.accept()

    def extend(self, delta: int) -> None:
        """Grow the drag by *delta* pixels, as if the pointer had moved that far.

        What the page's auto-scroll drives. While the pointer rests in the band
        at the edge of the window the page slides underneath it, and the row has
        to gain exactly what the viewport gave up — otherwise the grip creeps out
        from under the hand that is dragging it, which is the same complaint the
        frozen page height (see :meth:`RowStack.arm_grip`) exists to answer.

        Spelled as a shift of the *press* position rather than of the height, so
        the drag stays one absolute sum from where it began and cannot drift.
        """
        if not self._dragging or not delta:
            return
        self._press_y -= int(delta)
        self._restate_height()

    def _restate_height(self) -> None:
        """Re-derive the row's height from the press and the pointer, and say so."""
        moved = self._last_global.y() - self._press_y
        wanted = max(0, self._start_height + moved)
        settled = snap_to_detent(wanted, self._targets, int(theme.metric("grid.detent")))
        self.heightDragged.emit(settled)
        if settled != wanted:
            self.detentReached.emit(settled)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.update()
            self.dragFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hovered = event.type() == QEvent.Type.HoverEnter
            self.update()
        return super().event(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        del event
        paint_divider(self, QPainter(self), self._hovered, self._dragging, horizontal=False)


class _RowHolder(QWidget):
    """A wrapper the stack owns, so a row's own widget is never given a height.

    Exists for one reason, and it is a bug rather than tidiness: a row holding a
    single block *is* that block's frame, with no container of its own (see
    :func:`build_node`). Anything the stack set on it — a fixed height, a size
    policy — was therefore set on the block, and travelled with the block when it
    was later dragged into the pinned strip or popped out into a window.
    """

    def __init__(self, row: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._row = row
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(row)

    def row(self) -> QWidget:
        return self._row

    def release(self) -> QWidget:
        """Hand the row back, so this wrapper can be deleted without it."""
        self.layout().removeWidget(self._row)
        return self._row


class RowStack(QWidget):
    """The page: rows with an explicit-or-automatic height, and a grip under each.

    Held in the canvas's own layout rather than being a widget the canvas puts
    something into, so the drag controller, the drop indicator and the edge
    auto-scroll all go on working against the canvas exactly as they did.
    """

    #: A row was dragged to a new height; the canvas records it in the tree.
    heightsChanged = Signal()
    #: A row grip is being dragged, and the pointer is here. The canvas answers
    #: with :meth:`extend_active_drag` when that is at the edge of the window.
    gripDragMoved = Signal(QPoint)
    #: The grip has been let go.
    gripDragFinished = Signal()
    #: A row has been dragged small enough that letting go would close the blocks
    #: in it - or has been dragged back out of that. ``(index, closing)``.
    rowCollapsing = Signal(int, bool)
    #: The grip was let go with its row still that small.
    rowCollapsed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._rows: list[QWidget] = []
        self._holders: list[_RowHolder] = []
        self._grips: list[RowGrip] = []
        self._heights: list[int] = []
        # The grip currently being dragged, so the page's auto-scroll has
        # something to extend, and the height frozen for the duration of it.
        self._active_grip: RowGrip | None = None
        self._frozen_height = 0
        # The row currently warned as too short to keep (see _mark_collapse).
        self._collapsing_row: int | None = None
        # The same overlay a GridSplitter uses for its own detents, so a row grip
        # and a divider mark their recommended sizes identically. Built on the
        # first drag and destroyed on release (see :class:`_DetentMark`).
        self._mark: _DetentMark | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_rows(self, rows: Sequence[QWidget], heights: Sequence[int]) -> None:
        """Show *rows*, the nth at ``heights[n]`` pixels (or its content at zero)."""
        self._shed()
        self._rows = list(rows)
        self._heights = [int(height) if height and height > 0 else 0 for height in heights]
        self._heights += [0] * (len(self._rows) - len(self._heights))

        for index, row in enumerate(self._rows):
            holder = _RowHolder(row, self)
            self._holders.append(holder)
            self._apply_height(index)
            self._layout.addWidget(holder)
            row.show()
            grip = RowGrip(self)
            grip.armed.connect(lambda i=index: self.arm_grip(i))
            grip.heightDragged.connect(lambda value, i=index: self._on_dragged(i, value))
            grip.detentReached.connect(lambda value, i=index: self._on_dragged(i, value, True))
            grip.dragMoved.connect(self.gripDragMoved.emit)
            # Thaw first: the page's height must be its own again before anyone
            # asked to record the drag goes looking at it.
            grip.dragFinished.connect(self._end_grip_drag)
            grip.dragFinished.connect(self.clear_detent_marks)
            grip.dragFinished.connect(self.heightsChanged.emit)
            self._layout.addWidget(grip)
            self._grips.append(grip)
        # Slack below the last row stays empty rather than stretching the bottom
        # block: how tall a row is, is the user's answer now, not a leftover.
        self._layout.addStretch(1)

    def heights(self) -> list[int]:
        """The height of each row; zero for one that is still sized by its content."""
        return list(self._heights)

    # No ``minimumSizeHint`` here, and that is the point. It used to report the
    # rows' full height, so the page would overflow the viewport and scroll — but
    # computing ``sizeHint()`` *inside* ``minimumSizeHint()`` makes the minimum
    # depend on the width, which is the one thing a widget in a QScrollArea must
    # never do: the bar appears, the viewport narrows, the content gets taller,
    # the minimum grows, and the whole thing can chase itself down the stack. It
    # was never needed. A holder with a ``Minimum`` vertical policy already reports
    # its content height as its minimum, and one with a fixed height reports that,
    # so ``QVBoxLayout`` sums exactly the same number on its own (see
    # ``tests/test_grid_resize.py``). The same warning is written out in full on
    # :class:`~mm_companion.ui.flow_layout.FlowContainer`, which reached it first.

    # -- dragging -------------------------------------------------------------

    def _on_dragged(self, index: int, height: int, settled: bool = False) -> None:
        if not 0 <= index < len(self._rows):
            return
        self._heights[index] = max(0, int(height))
        self._apply_height(index)
        self._mark_collapse(index, self._heights[index])
        if 0 <= index < len(self._grips):
            self._mark_rows(index, self._grips[index].targets, height if settled else None)

    def _apply_height(self, index: int) -> None:
        """Pin the *holder* to a height, or let it follow its content again.

        Always the holder and never the row itself. A row holding one block *is*
        that block's frame, so setting a height or a size policy on it would
        change the block permanently — and it did: a frame that had once been a
        lone row carried a ``Minimum`` vertical policy with it into the pinned
        strip, where it then refused to be squashed. The holder is a wrapper
        nobody else ever sees, so nothing it is told can follow a block around.
        """
        holder = self._holders[index]
        height = self._heights[index]
        if height > 0:
            holder.setFixedHeight(height)
            holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.updateGeometry()
            return
        # Back to "as tall as what is in it": clear the fixed height Qt keeps as a
        # min *and* a max, or the row would be stuck at whatever it was last given.
        holder.setMinimumHeight(0)
        holder.setMaximumHeight(16777215)
        holder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.updateGeometry()

    def arm_grip(self, index: int) -> None:
        """Tell the grip under row *index* how tall that row is, as it is pressed.

        The height comes from the *holder*, which is what actually has one: a row
        that has never been dragged states no height of its own and its holder is
        as tall as whatever is in it.
        """
        if not 0 <= index < len(self._grips):
            return
        holder = self._holders[index]
        height = self._heights[index] or holder.height() or holder.sizeHint().height()
        if height <= 0:
            return  # the grip refuses the drag; nothing to freeze or mark
        targets = self._row_detents(index)
        grip = self._grips[index]
        grip.begin_from(height, targets)
        self._active_grip = grip
        self._freeze()
        self._mark_rows(index, targets, None)

    def extend_active_drag(self, delta: int) -> None:
        """Grow the grip currently being dragged by *delta* (see :meth:`RowGrip.extend`)."""
        if self._active_grip is not None:
            self._active_grip.extend(delta)

    def _mark_collapse(self, index: int, height: int) -> None:
        """Warn, or stop warning, that this row is being dragged out of existence.

        The row's counterpart of :meth:`GridSplitter.update_collapse_marks`, and
        it takes the *whole row* with it: a row nought pixels tall is every block
        in it gone, so saying so about only one of them would be a lie.
        """
        limit = int(theme.metric("grid.close-extent"))
        wanted = index if limit > 0 and height < limit else None
        if wanted == self._collapsing_row:
            return
        if self._collapsing_row is not None:
            self.rowCollapsing.emit(self._collapsing_row, False)
        self._collapsing_row = wanted
        if wanted is not None:
            self.rowCollapsing.emit(wanted, True)

    def _clear_collapse_mark(self) -> int | None:
        """Take the warning down, and say which row it was on."""
        index, self._collapsing_row = self._collapsing_row, None
        if index is not None:
            self.rowCollapsing.emit(index, False)
        return index

    def _freeze(self) -> None:
        """Hold the page's total height for the length of one grip drag.

        Every mouse-move of a grip pins its row's holder to a new height, so the
        stack re-sums and the *page* gets shorter the instant a row does. Behind a
        ``QScrollArea`` that is not a cosmetic detail: the scroll value is clamped
        to the smaller maximum, the content slides down inside the viewport, and
        the divider the user is dragging sits still on screen while their hand
        walks away from it. Dragging a bottom row shorter was close to unusable
        for exactly this reason.

        So the stack refuses to get shorter until the drag is over, and the slack
        goes into the trailing stretch it already has. This is **not** the
        content-shaped minimum the page's shape rule forbids: it is one number
        read off the widget's own height at the instant of a mouse press, it is
        not a function of the width, nothing recomputes it while it stands, and it
        is gone again on release. Growing is unaffected — a row dragged taller
        still pushes the page past the viewport, which is what makes it scroll.
        """
        self._frozen_height = max(0, self.height())
        self.setMinimumHeight(self._frozen_height)

    def _end_grip_drag(self) -> None:
        """The grip was let go: thaw, stand the auto-scroll down, act on the mark."""
        collapsed = self._clear_collapse_mark()
        self._cancel_grip_drag()
        if collapsed is not None:
            self.rowCollapsed.emit(collapsed)

    def _cancel_grip_drag(self) -> None:
        """Let go of the drag without deciding anything by it.

        What a rebuild *under* a live drag gets. The freeze has to come off or the
        page keeps a height nobody asked for, but a row that happens to be short
        at that moment was not dragged there by anybody, and closing its blocks
        would be a gesture the user never made.
        """
        self._clear_collapse_mark()
        self._active_grip = None
        self._frozen_height = 0
        self.setMinimumHeight(0)
        self.gripDragFinished.emit()

    def _mark_rows(self, index: int, targets: Sequence[int], settled: int | None) -> None:
        """Show where this row's recommended heights are, while it is being dragged.

        The heights are turned into positions down the page here, because that is
        the only difference between marking a row and marking a divider — the
        overlay itself is the one a :class:`GridSplitter` uses, unchanged.
        """
        if not 0 <= index < len(self._holders):
            return
        top = self._holders[index].y()
        mark = self._ensure_mark()
        if mark is None:
            return
        mark.setGeometry(self.rect())
        mark.show_targets(
            [top + target for target in targets],
            None if settled is None else top + settled,
            horizontal=False,
        )

    def _ensure_mark(self) -> _DetentMark | None:
        if self._mark is None:
            self._mark = _DetentMark(self)
        return self._mark

    def clear_detent_marks(self) -> None:
        """Take the mark down and destroy it — see :meth:`GridSplitter.clear_detent_marks`."""
        mark, self._mark = self._mark, None
        if mark is not None:
            discard_widget(mark)

    def _row_detents(self, index: int) -> list[int]:
        """The heights row *index* reads well at.

        Two of them, and the first is the more useful: **fit to content**, the
        height at which nothing in the row has to scroll. The second is what the
        blocks recommend, which is what they open at. A row nobody has dragged is
        already at the first, so the mark under an untouched row shows you what you
        are about to leave.
        """
        row = self._rows[index]
        wanted = {row.sizeHint().height(), _recommended_extent(row, horizontal=False)}
        return sorted(height for height in wanted if height > 0)

    def _shed(self) -> None:
        # A rebuild in the middle of a drag would leave the freeze standing with
        # no grip left to release it.
        self._cancel_grip_drag()
        while self._layout.count():
            self._layout.takeAt(0)
        for grip in self._grips:
            discard_widget(grip)
        self._grips = []
        # A holder is ours and goes; the row inside it is the caller's, and is
        # released first so deleting the wrapper cannot take a live block with it.
        for holder in self._holders:
            holder.release()
            discard_widget(holder)
        self._holders = []
        self._rows = []


def build_node(node: Node, build_leaf: LeafBuilder, parent: QWidget | None = None) -> QWidget:
    """Render *node* — a leaf's widget, or a splitter of its children's."""
    if isinstance(node, Leaf):
        widget = build_leaf(node)
        if parent is not None:
            widget.setParent(parent)
        return widget

    orientation = (
        Qt.Orientation.Horizontal if node.orientation == HORIZONTAL else Qt.Orientation.Vertical
    )
    splitter = GridSplitter(orientation, parent)
    for child in node.children:
        splitter.addWidget(build_node(child, build_leaf, splitter))
    sizes = node.usable_sizes()
    # A splitter takes its sizes all together or not at all, so a run with any
    # child still unsized is laid out from every child's hint instead of slotting
    # a remembered number in beside a natural one — the mistake that once handed a
    # moved block a sliver of the pinned strip.
    if sizes and all(size > 0 for size in sizes):
        splitter.setSizes(list(sizes))
    return splitter


def split_sizes(widget: QWidget) -> tuple[int, ...]:
    """The live sizes of *widget* if it is a split, else nothing."""
    return tuple(widget.sizes()) if isinstance(widget, QSplitter) else ()


def frame_at(widget: QWidget, point: QPoint) -> QWidget | None:
    """The deepest child of *widget* containing *point*, in *widget*'s coordinates."""
    found = widget.childAt(point)
    return found if found is not None else None
